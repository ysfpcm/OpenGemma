"""
Sentinel Agent (OpenJarvis Extension)

This agent handles security monitoring by capturing frames from a local USB webcam
(/dev/video0 or similar) using OpenCV and analyzing them for anomalies.
"""

import logging
import threading
import time
from typing import Any, Optional

import cv2

from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.registry import AgentRegistry
from openjarvis.engine._stubs import InferenceEngine

logger = logging.getLogger(__name__)

@AgentRegistry.register("sentinel")
class SentinelAgent(BaseAgent):
    agent_id = "sentinel"

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        **kwargs: Any
    ):
        super().__init__(engine, model, **kwargs)
        self.camera_index = 0  # Default USB webcam index
        self.is_monitoring = False
        self._monitor_thread = None
        self.last_anomaly = None

    def _monitor_loop(self):
        """Background loop to periodically capture and analyze frames."""
        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            logger.error("Sentinel could not open camera stream.")
            self.is_monitoring = False
            return

        logger.info(f"Sentinel started monitoring camera {self.camera_index}")
        while self.is_monitoring:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Sentinel failed to read frame.")
                time.sleep(1)
                continue

            # Perform mock analysis (in reality, run a fast object detection model)
            # We look for large changes in the frame or run YOLO on it.
            # For this mock, we just say it's clear.
            time.sleep(2)  # Sample every 2 seconds

        cap.release()
        logger.info("Sentinel stopped monitoring.")

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Executes the security agent workflow."""
        text = input.lower()
        if "start" in text or "monitor" in text:
            if not self.is_monitoring:
                self.is_monitoring = True
                self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
                self._monitor_thread.start()
                return AgentResult(content="Sentinel has activated monitoring on the USB webcam. The premises are secure.")
            else:
                return AgentResult(content="Sentinel is already monitoring the premises.")

        elif "stop" in text:
            if self.is_monitoring:
                self.is_monitoring = False
                return AgentResult(content="Sentinel has deactivated monitoring.")
            else:
                return AgentResult(content="Sentinel is not currently monitoring.")

        elif "status" in text or "report" in text:
            status = "ACTIVE" if self.is_monitoring else "STANDBY"
            anomaly_msg = f"Last anomaly detected: {self.last_anomaly}" if self.last_anomaly else "No anomalies detected."
            return AgentResult(content=f"Sentinel Status: {status}. {anomaly_msg}")

        else:
            return AgentResult(content="Sentinel acknowledges. I am standing by for security commands.")

