"""
Gmail Parser for Financial Alerts (Wells Fargo)

This script automates parsing of emails specifically from the 'Financial-Alerts' label.
It fetches unread messages, uses Regex matrices to isolate Wells Fargo transaction data,
saves to the local PostgreSQL database, and strips the UNREAD attribute.

It reuses OpenJarvis's native Google OAuth token management to avoid manual flow setup.
"""

import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
import psycopg2

from openjarvis.connectors.oauth import (
    load_tokens,
    refresh_google_token,
    resolve_google_credentials,
)
from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

_GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_DEFAULT_CREDENTIALS_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "gmail.json")

class FinancialEmailParser:
    def __init__(self, db_url: str = None):
        """Initializes the parser with DB connection and resolved credentials path."""
        self.db_url = db_url or os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/openjarvis"
        )
        self.conn = self._get_db_connection()
        self.credentials_path = resolve_google_credentials(_DEFAULT_CREDENTIALS_PATH)

    def _get_db_connection(self):
        try:
            return psycopg2.connect(self.db_url)
        except Exception as e:
            logger.error(f"PostgreSQL Connection Error: {e}")
            raise

    def _get_token(self) -> str:
        """Loads and returns the cached Google access token."""
        tokens = load_tokens(self.credentials_path)
        if not tokens:
            raise RuntimeError(f"Gmail not connected. Stored tokens not found at {self.credentials_path}")
        return tokens.get("token") or tokens.get("access_token") or ""

    def _call_api_with_refresh(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Calls Gmail API, automatically refreshing token on 401 Unauthorized."""
        token = self._get_token()
        headers = kwargs.setdefault("headers", {})
        headers["Authorization"] = f"Bearer {token}"

        try:
            resp = httpx.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.info("Access token expired. Refreshing token...")
                new_token = refresh_google_token(self.credentials_path)
                if not new_token:
                    raise RuntimeError("Failed to refresh Gmail OAuth token.")
                # Retry with new token
                headers["Authorization"] = f"Bearer {new_token}"
                resp = httpx.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            raise

    def fetch_unread_financial_alerts(self) -> List[Dict[str, Any]]:
        """
        Fetches unread messages under the 'Financial-Alerts' label using search query.
        """
        query = "is:unread label:Financial-Alerts -from:noreply@mail.calai.app"
        url = f"{_GMAIL_API_BASE}/messages"

        resp = self._call_api_with_refresh("GET", url, params={"q": query})
        messages = resp.json().get('messages', [])
        logger.info(f"Found {len(messages)} unread financial alerts.")
        return messages

    def fetch_unread_shopping_alerts(self) -> List[Dict[str, Any]]:
        """
        Fetches unread messages from Amazon or shopping labels for the Quartermaster.
        """
        query = "is:unread (from:amazon.com OR label:Shopping)"
        url = f"{_GMAIL_API_BASE}/messages"

        resp = self._call_api_with_refresh("GET", url, params={"q": query})
        messages = resp.json().get('messages', [])
        logger.info(f"Found {len(messages)} unread shopping alerts.")
        return messages

    def parse_wells_fargo_email(self, email_body: str) -> Dict[str, Any]:
        """
        Regex matrices tailored to standard Wells Fargo notification structures.
        Expects to extract: Transaction Amount, Merchant, Account/Card (last 4), and Available Balance.
        """
        data = {
            "amount": None,
            "merchant": None,
            "account_identifier": None,
            "current_balance": None
        }

        # Regex patterns tailored to Wells Fargo alerts
        # E.g., "A purchase of $125.50 was authorized at TARGET..."
        amount_match = re.search(r'\$(\d+\.\d{2})', email_body)
        if amount_match:
            data['amount'] = float(amount_match.group(1))

        # E.g., "at TARGET on 07/02/2026" or "Merchant: TARGET"
        is_declined = "declined" in email_body.lower() or "decline" in email_body.lower()
        merchant_match = re.search(r'(?:at|Merchant:)\s+([A-Za-z0-9\s]+?)(?:\s+on|\.|\s*$|<)', email_body)
        if merchant_match:
            merchant_name = merchant_match.group(1).strip()
            if is_declined:
                data['merchant'] = f"DECLINED: {merchant_name}"
            else:
                data['merchant'] = merchant_name

        # E.g., "Account ending in 1234" or "Card ending in 1234"
        account_match = re.search(r'(?:Account|Card) ending in (\d{4})', email_body)
        if account_match:
            data['account_identifier'] = account_match.group(1)

        # E.g., "Available Balance: $1,234.56"
        balance_match = re.search(r'Available Balance:\s*\$([\d,]+\.\d{2})', email_body)
        if balance_match:
            data['current_balance'] = float(balance_match.group(1).replace(',', ''))

        return data

    def _decode_body(self, payload: Dict[str, Any]) -> str:
        """Decode the message body from Gmail payload."""
        mime_type = payload.get("mimeType", "")

        if mime_type.startswith("multipart/"):
            parts = payload.get("parts", [])
            # Try plain text first
            for part in parts:
                if part.get("mimeType", "").startswith("text/plain"):
                    return self._decode_body(part)
            # Try HTML second
            for part in parts:
                if part.get("mimeType", "").startswith("text/html"):
                    return self._decode_body(part)
            if parts:
                return self._decode_body(parts[0])
            return ""

        body_data = payload.get("body", {}).get("data", "")
        if not body_data:
            return ""

        padded = body_data + "=" * (-len(body_data) % 4)
        try:
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except Exception:
            return ""

    def process_alerts(self):
        """Main execution loop for parsing and storing alerts."""
        messages = self.fetch_unread_financial_alerts()
        cursor = self.conn.cursor()

        for msg in messages:
            msg_id = msg['id']
            try:
                # Fetch full message
                url = f"{_GMAIL_API_BASE}/messages/{msg_id}"
                message = self._call_api_with_refresh("GET", url, params={"format": "full"}).json()

                payload = message['payload']
                body = self._decode_body(payload)

                # Internal Timestamp from email
                internal_date = int(message['internalDate']) / 1000.0
                transaction_date = datetime.fromtimestamp(internal_date, tz=timezone.utc)

                # Parse
                parsed_data = self.parse_wells_fargo_email(body)

                # Require minimum data to log transaction
                if parsed_data['amount'] is not None and parsed_data['account_identifier'] is not None:
                    # Insert into financial_ledger
                    cursor.execute(
                        """
                        INSERT INTO financial_ledger 
                        (transaction_date, amount, merchant, account_identifier, raw_email_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (raw_email_id) DO NOTHING
                        """,
                        (
                            transaction_date,
                            parsed_data['amount'],
                            parsed_data['merchant'] or 'Unknown',
                            parsed_data['account_identifier'],
                            msg_id
                        )
                    )

                    # Update account_balances if present
                    if parsed_data['current_balance'] is not None:
                        cursor.execute(
                            """
                            INSERT INTO account_balances (account_identifier, current_balance, last_updated)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (account_identifier) 
                            DO UPDATE SET current_balance = EXCLUDED.current_balance, last_updated = EXCLUDED.last_updated
                            """,
                            (parsed_data['account_identifier'], parsed_data['current_balance'], transaction_date)
                        )

                    self.conn.commit()
                    logger.info(f"Processed transaction {msg_id} successfully.")

                # Mark as read (remove UNREAD label)
                modify_url = f"{_GMAIL_API_BASE}/messages/{msg_id}/modify"
                self._call_api_with_refresh("POST", modify_url, json={'removeLabelIds': ['UNREAD']})
                logger.info(f"Stripped UNREAD attribute from {msg_id}.")

            except Exception as e:
                self.conn.rollback()
                logger.error(f"Failed to process message {msg_id}: {e}")

        cursor.close()

    def close(self):
        if self.conn:
            self.conn.close()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    parser = FinancialEmailParser()
    parser.process_alerts()
    parser.close()
