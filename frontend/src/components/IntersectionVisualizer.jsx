import React from 'react';

export default function IntersectionVisualizer({ status }) {
  const desired = status?.desired_signals || { NORTH: 'RED', SOUTH: 'RED', EAST: 'RED', WEST: 'RED' };
  const actual = status?.actual_signals || { NORTH: 'UNKNOWN', SOUTH: 'UNKNOWN', EAST: 'UNKNOWN', WEST: 'UNKNOWN' };
  const queues = status?.queues || { NORTH: 0, SOUTH: 0, EAST: 0, WEST: 0 };
  const currentPhase = status?.phase || 'NORTH_SOUTH';

  const getColorHex = (color) => {
    switch (color) {
      case 'GREEN': return '#10b981';
      case 'YELLOW': return '#f59e0b';
      case 'RED': return '#ef4444';
      default: return '#64748b';
    }
  };

  const renderSignalHead = (direction, label, x, y, rotation = 0) => {
    const desColor = desired[direction] || 'RED';
    const actColor = actual[direction] || 'UNKNOWN';
    const isMismatch = desColor !== actColor && actColor !== 'UNKNOWN';

    return (
      <g transform={`translate(${x}, ${y}) rotate(${rotation})`}>
        {/* Signal housing */}
        <rect
          x="-18"
          y="-36"
          width="36"
          height="72"
          rx="8"
          fill="#0f172a"
          stroke={isMismatch ? '#f43f5e' : '#334155'}
          strokeWidth={isMismatch ? 2.5 : 1.5}
          filter="drop-shadow(0px 4px 8px rgba(0,0,0,0.6))"
        />
        {/* Red light */}
        <circle
          cx="0"
          cy="-20"
          r="8"
          fill={desColor === 'RED' ? '#ef4444' : '#334155'}
          filter={desColor === 'RED' ? 'drop-shadow(0 0 8px rgba(239, 68, 68, 0.9))' : 'none'}
        />
        {/* Yellow light */}
        <circle
          cx="0"
          cy="0"
          r="8"
          fill={desColor === 'YELLOW' ? '#f59e0b' : '#334155'}
          filter={desColor === 'YELLOW' ? 'drop-shadow(0 0 8px rgba(245, 158, 11, 0.9))' : 'none'}
        />
        {/* Green light */}
        <circle
          cx="0"
          cy="20"
          r="8"
          fill={desColor === 'GREEN' ? '#10b981' : '#334155'}
          filter={desColor === 'GREEN' ? 'drop-shadow(0 0 10px rgba(16, 185, 129, 0.9))' : 'none'}
        />

        {/* Direction badge */}
        <text
          x="0"
          y="48"
          textAnchor="middle"
          fill="#94a3b8"
          fontSize="10"
          fontWeight="700"
          fontFamily="Outfit, sans-serif"
        >
          {label}
        </text>

        {/* Mismatch badge if desired != actual */}
        {isMismatch && (
          <text
            x="0"
            y="-44"
            textAnchor="middle"
            fill="#f43f5e"
            fontSize="9"
            fontWeight="bold"
          >
            ACTUAL: {actColor}
          </text>
        )}
      </g>
    );
  };

  const renderVehicles = (direction, count) => {
    const items = [];
    const maxVisual = Math.min(count, 5);
    for (let i = 0; i < maxVisual; i++) {
      items.push(i);
    }
    return items;
  };

  return (
    <div className="visualizer-container" style={{ position: 'relative', width: '100%', height: '420px', display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
      <svg width="420" height="420" viewBox="0 0 420 420" style={{ maxWidth: '100%', maxHeight: '100%' }}>
        {/* Background grass/ground */}
        <rect width="420" height="420" rx="16" fill="#0b1120" />

        {/* North-South Road */}
        <rect x="150" y="0" width="120" height="420" fill="#1e293b" />
        {/* East-West Road */}
        <rect x="0" y="150" width="420" height="120" fill="#1e293b" />

        {/* Road center dividers (dashed lines) */}
        {/* North road line */}
        <line x1="210" y1="0" x2="210" y2="150" stroke="#f8fafc" strokeWidth="2.5" strokeDasharray="10 10" opacity="0.4" />
        {/* South road line */}
        <line x1="210" y1="270" x2="210" y2="420" stroke="#f8fafc" strokeWidth="2.5" strokeDasharray="10 10" opacity="0.4" />
        {/* West road line */}
        <line x1="0" y1="210" x2="150" y2="210" stroke="#f8fafc" strokeWidth="2.5" strokeDasharray="10 10" opacity="0.4" />
        {/* East road line */}
        <line x1="270" y1="210" x2="420" y2="210" stroke="#f8fafc" strokeWidth="2.5" strokeDasharray="10 10" opacity="0.4" />

        {/* Intersection center area highlight */}
        <rect
          x="150"
          y="150"
          width="120"
          height="120"
          fill="#172554"
          opacity="0.3"
        />

        {/* Crosswalk markings */}
        {/* North crosswalk */}
        <line x1="150" y1="145" x2="270" y2="145" stroke="#94a3b8" strokeWidth="3" opacity="0.5" />
        {/* South crosswalk */}
        <line x1="150" y1="275" x2="270" y2="275" stroke="#94a3b8" strokeWidth="3" opacity="0.5" />
        {/* West crosswalk */}
        <line x1="145" y1="150" x2="145" y2="270" stroke="#94a3b8" strokeWidth="3" opacity="0.5" />
        {/* East crosswalk */}
        <line x1="275" y1="150" x2="275" y2="270" stroke="#94a3b8" strokeWidth="3" opacity="0.5" />

        {/* Queue Visual Indicators (Cars waiting) */}
        {/* North vehicles waiting */}
        {renderVehicles('NORTH', queues.NORTH).map((i) => (
          <rect
            key={`n-${i}`}
            x="175"
            y={120 - i * 22}
            width="22"
            height="16"
            rx="4"
            fill="#38bdf8"
            opacity="0.85"
            stroke="#0284c7"
          />
        ))}

        {/* South vehicles waiting */}
        {renderVehicles('SOUTH', queues.SOUTH).map((i) => (
          <rect
            key={`s-${i}`}
            x="223"
            y={285 + i * 22}
            width="22"
            height="16"
            rx="4"
            fill="#38bdf8"
            opacity="0.85"
            stroke="#0284c7"
          />
        ))}

        {/* East vehicles waiting */}
        {renderVehicles('EAST', queues.EAST).map((i) => (
          <rect
            key={`e-${i}`}
            x={285 + i * 22}
            y="175"
            width="16"
            height="22"
            rx="4"
            fill="#38bdf8"
            opacity="0.85"
            stroke="#0284c7"
          />
        ))}

        {/* West vehicles waiting */}
        {renderVehicles('WEST', queues.WEST).map((i) => (
          <rect
            key={`w-${i}`}
            x={120 - i * 22}
            y="223"
            width="16"
            height="22"
            rx="4"
            fill="#38bdf8"
            opacity="0.85"
            stroke="#0284c7"
          />
        ))}

        {/* Traffic Signals at 4 corners */}
        {/* North signal */}
        {renderSignalHead('NORTH', 'NORTH', 120, 110, 0)}
        {/* South signal */}
        {renderSignalHead('SOUTH', 'SOUTH', 300, 310, 0)}
        {/* East signal */}
        {renderSignalHead('EAST', 'EAST', 310, 120, 0)}
        {/* West signal */}
        {renderSignalHead('WEST', 'WEST', 110, 300, 0)}

        {/* Center Intersection Phase Display */}
        <circle cx="210" cy="210" r="32" fill="#0f172a" stroke="#334155" strokeWidth="2" />
        <text
          x="210"
          y="207"
          textAnchor="middle"
          fill="#38bdf8"
          fontSize="11"
          fontWeight="bold"
          fontFamily="Outfit, sans-serif"
        >
          {currentPhase === 'NORTH_SOUTH' ? 'N ↕ S' : 'E ↔ W'}
        </text>
        <text
          x="210"
          y="222"
          textAnchor="middle"
          fill="#94a3b8"
          fontSize="8.5"
          fontWeight="600"
        >
          PHASE
        </text>
      </svg>
    </div>
  );
}
