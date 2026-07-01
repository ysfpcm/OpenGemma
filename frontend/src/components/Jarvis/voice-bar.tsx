"use client"

import { Mic, MicOff, Send, Volume2, VolumeX } from "lucide-react"
import { useState } from "react"
import { cn } from "../../lib/utils"

const SUGGESTIONS = [
  "Status check",
  "What files do I have on Google Drive?",
  "Summarize my recent emails",
  "Any tasks due today?",
  "Run a security audit",
  "System diagnostics",
]

type VoiceBarProps = {
  supported: boolean
  listening: boolean
  interim: string
  busy: boolean
  onToggleMic: () => void
  onSubmit: (text: string, isVoiceInput?: boolean) => void
  ttsEnabled?: boolean
  onToggleTts?: () => void
}

export function VoiceBar({
  supported,
  listening,
  interim,
  busy,
  onToggleMic,
  onSubmit,
  ttsEnabled = false,
  onToggleTts,
}: VoiceBarProps) {
  const [text, setText] = useState("")

  const submit = () => {
    const value = text.trim()
    if (!value) return
    onSubmit(value, false) // Explicitly false for typed text
    setText("")
  }

  return (
    <div className="flex w-full flex-col items-center gap-3">
      {/* Suggestions Chips */}
      <div className="flex flex-wrap items-center justify-center gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSubmit(s, false)}
            disabled={busy}
            className="glass rounded-full px-3 py-1 font-mono text-[9px] tracking-wide text-muted-foreground transition-colors hover:text-primary disabled:opacity-40"
          >
            {s}
          </button>
        ))}
      </div>

      {/* Main Command Input Bar */}
      <div className="glass-strong flex w-full max-w-2xl items-center gap-2 rounded-full p-1.5 pl-4">
        <button
          type="button"
          onClick={onToggleMic}
          disabled={!supported}
          aria-label={listening ? "Stop listening" : "Start listening"}
          className={cn(
            "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition-colors",
            listening
              ? "bg-primary text-primary-foreground"
              : "border border-primary/50 text-primary hover:bg-primary/10",
            !supported && "cursor-not-allowed opacity-40"
          )}
        >
          {listening && (
            <span className="absolute inset-0 rounded-full bg-primary/40 animate-pulse-ring" />
          )}
          {supported ? <Mic className="h-5 w-5" /> : <MicOff className="h-5 w-5" />}
        </button>

        <input
          value={listening && interim ? interim : text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing && e.keyCode !== 229) submit()
          }}
          readOnly={listening}
          placeholder={
            listening
              ? "Recording voice..."
              : supported
                ? "Speak or type a command to Gemma..."
                : "Type a command to Gemma..."
          }
          className="flex-1 bg-transparent font-mono text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none"
        />

        <button
          type="button"
          onClick={submit}
          disabled={busy || !text.trim()}
          aria-label="Send command"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-primary/50 text-primary transition-colors hover:bg-primary/10 disabled:opacity-30"
        >
          <Send className="h-4 w-4" />
        </button>

        {onToggleTts && (
          <button
            type="button"
            onClick={onToggleTts}
            aria-label={ttsEnabled ? "Disable Voice Responses" : "Enable Voice Responses"}
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-full border transition-colors",
              ttsEnabled 
                ? "border-primary/50 text-primary hover:bg-primary/10" 
                : "border-muted text-muted-foreground hover:bg-muted/10"
            )}
          >
            {ttsEnabled ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
          </button>
        )}
      </div>
      {!supported && (
        <p className="font-mono text-[9px] tracking-wide text-muted-foreground">
          Voice recording requires a microphone to be enabled and allowed in your settings.
        </p>
      )}
    </div>
  )
}
