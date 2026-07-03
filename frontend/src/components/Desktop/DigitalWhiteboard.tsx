import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';

const C = {
  bg: '#1e1e2e',
  mantle: '#181825',
  surface0: '#313244',
  text: '#cdd6f4',
  subtext0: '#a6adc8',
  accent: '#89b4fa',
  border: '#45475a',
};

export function DigitalWhiteboard({ agentId }: { agentId: string }) {
  // In a real implementation, we would fetch artifacts from the backend for this agent
  const [artifacts, setArtifacts] = useState<any[]>([
    { id: '1', name: 'implementation_plan.md', type: 'plan', content: '# Implementation Plan\\n\\nWaiting for Architect to generate a plan...', status: 'pending' }
  ]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 16 }}>
      <div style={{ color: C.subtext0, fontSize: 13, fontWeight: 500, borderBottom: `1px solid ${C.border}`, paddingBottom: 8 }}>
        Architect Digital Whiteboard
      </div>
      
      <div style={{ display: 'flex', flex: 1, gap: 16 }}>
        {/* Artifact List */}
        <div style={{ width: '200px', borderRight: `1px solid ${C.border}`, paddingRight: 16 }}>
          <div style={{ color: C.text, fontSize: 12, marginBottom: 8, fontWeight: 600 }}>ARTIFACTS</div>
          {artifacts.map(art => (
            <div key={art.id} style={{ 
              padding: '8px', 
              background: C.surface0, 
              borderRadius: '6px', 
              fontSize: '12px',
              color: C.text,
              cursor: 'pointer',
              marginBottom: 8,
              border: `1px solid ${C.border}`
            }}>
              📄 {art.name}
            </div>
          ))}
        </div>
        
        {/* Artifact Viewer */}
        <div style={{ flex: 1, overflowY: 'auto', padding: 16, background: C.mantle, borderRadius: 8, border: `1px solid ${C.border}` }}>
          <div style={{ color: C.text, fontSize: 14 }}>
            <ReactMarkdown>{artifacts[0].content}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
