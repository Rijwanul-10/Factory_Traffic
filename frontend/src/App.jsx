import React, { useState, useEffect } from 'react';
import {
  ShieldAlert,
  AlertTriangle,
  Radio,
  Timer,
  Sliders,
  History,
  Car,
  CheckCircle,
  RefreshCw,
  PowerOff,
  Flame,
  ArrowRightLeft,
} from 'lucide-react';
import IntersectionVisualizer from './components/IntersectionVisualizer';
import SimulationForm from './components/SimulationForm';

export default function App() {
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [isBackendConnected, setIsBackendConnected] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [manualLoading, setManualLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState(null);

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/junctions/A/status');
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
        setIsBackendConnected(true);
        setLastUpdated(new Date());
      } else {
        setIsBackendConnected(false);
      }
    } catch (err) {
      setIsBackendConnected(false);
    }
  };

  const fetchHistory = async () => {
    try {
      const res = await fetch('/api/junctions/A/history?limit=15');
      if (res.ok) {
        const data = await res.json();
        setHistory(data);
      }
    } catch (err) {
      // Handled silently
    }
  };

  useEffect(() => {
    fetchStatus();
    fetchHistory();
    const interval = setInterval(() => {
      fetchStatus();
      fetchHistory();
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const showActionMessage = (msg, isError = false) => {
    setActionMessage({ text: msg, isError });
    setTimeout(() => setActionMessage(null), 3500);
  };

  const handleManualRequest = async (direction) => {
    setManualLoading(true);
    try {
      const res = await fetch('/api/junctions/A/commands', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command: 'MANUAL_GREEN_REQUEST',
          direction: direction,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showActionMessage(`Manual GREEN requested for ${direction} (${data.status})`);
        fetchStatus();
      } else {
        showActionMessage(data.detail || 'Manual request rejected', true);
      }
    } catch (err) {
      showActionMessage(`Error: ${err.message}`, true);
    } finally {
      setManualLoading(false);
    }
  };

  const handleReturnToAutomatic = async () => {
    setManualLoading(true);
    try {
      const res = await fetch('/api/junctions/A/commands', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          command: 'RETURN_TO_AUTOMATIC',
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showActionMessage('Returned to AUTOMATIC mode');
        fetchStatus();
      } else {
        showActionMessage(data.detail || 'Return failed', true);
      }
    } catch (err) {
      showActionMessage(`Error: ${err.message}`, true);
    } finally {
      setManualLoading(false);
    }
  };

  const mode = status?.mode || 'AUTOMATIC';
  const controllerStatus = status?.controller_status || 'ONLINE';
  const alerts = status?.alerts || [];
  const countdown = status?.countdown != null ? Math.ceil(status.countdown) : null;
  const queues = status?.queues || { NORTH: 0, SOUTH: 0, EAST: 0, WEST: 0 };
  const desired = status?.desired_signals || {};
  const actual = status?.actual_signals || {};

  return (
    <div style={{ maxWidth: '1440px', margin: '0 auto', padding: '24px 20px', minHeight: '100vh' }}>
      {/* ─── Top Navbar ──────────────────────────────────────────────────────── */}
      <header className="glass-panel" style={{ padding: '16px 24px', marginBottom: '20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{ background: 'linear-gradient(135deg, #0284c7, #6366f1)', padding: '10px', borderRadius: '12px', display: 'flex' }}>
            <Radio size={22} color="#ffffff" />
          </div>
          <div>
            <h1 style={{ fontSize: '1.35rem', fontWeight: 800, letterSpacing: '-0.02em', background: 'linear-gradient(135deg, #f8fafc, #94a3b8)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
              Factory Traffic Management System
            </h1>
            <p style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
              Junction A • Internal Garment Facility Roads
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          {/* 2x demo speed badge */}
          <span className="badge badge-demo">
            <Timer size={14} /> 2x Demo Speed
          </span>

          {/* Operating Mode Badge */}
          <span className={`badge badge-${mode.toLowerCase()}`}>
            {mode === 'EMERGENCY' && <Flame size={14} />}
            {mode === 'FAILURE' && <PowerOff size={14} />}
            MODE: {mode}
          </span>

          {/* Controller Status Badge */}
          <span className="badge" style={{
            background: controllerStatus === 'ONLINE' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.2)',
            color: controllerStatus === 'ONLINE' ? '#34d399' : '#f87171',
            border: `1px solid ${controllerStatus === 'ONLINE' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.5)'}`
          }}>
            CTRL: {controllerStatus}
          </span>

          {/* Backend connection pill */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: isBackendConnected ? '#34d399' : '#f87171' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: isBackendConnected ? '#10b981' : '#ef4444' }} />
            {isBackendConnected ? 'Backend Live' : 'Disconnected'}
          </div>
        </div>
      </header>

      {/* ─── Feedback or Alert Banner ────────────────────────────────────────── */}
      {actionMessage && (
        <div style={{
          padding: '12px 18px',
          borderRadius: '12px',
          marginBottom: '16px',
          fontSize: '0.875rem',
          fontWeight: 600,
          background: actionMessage.isError ? 'rgba(239, 68, 68, 0.25)' : 'rgba(16, 185, 129, 0.25)',
          color: actionMessage.isError ? '#f87171' : '#34d399',
          border: `1px solid ${actionMessage.isError ? '#ef4444' : '#10b981'}`,
        }}>
          {actionMessage.text}
        </div>
      )}

      {/* Emergency Mode Banner */}
      {mode === 'EMERGENCY' && (
        <div className="glass-panel" style={{
          padding: '14px 20px',
          marginBottom: '16px',
          background: 'rgba(244, 63, 94, 0.2)',
          borderColor: 'rgba(244, 63, 94, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Flame size={24} color="#fb7185" />
            <div>
              <div style={{ fontWeight: 800, color: '#fb7185', fontSize: '0.95rem' }}>
                EMERGENCY PREEMPTION ACTIVE
              </div>
              <div style={{ fontSize: '0.8rem', color: '#fda4af' }}>
                Emergency vehicle: {status?.active_emergency?.vehicle_id || 'Approaching'} from {status?.active_emergency?.direction || 'Conflicting phase'}
              </div>
            </div>
          </div>
          <span className="badge badge-emergency">Preemption in progress</span>
        </div>
      )}

      {/* Manual Mode Banner */}
      {mode === 'MANUAL' && (
        <div className="glass-panel" style={{
          padding: '14px 20px',
          marginBottom: '16px',
          background: 'rgba(245, 158, 11, 0.2)',
          borderColor: 'rgba(245, 158, 11, 0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Sliders size={24} color="#fbbf24" />
            <div>
              <div style={{ fontWeight: 800, color: '#fbbf24', fontSize: '0.95rem' }}>
                MANUAL OVERRIDE ACTIVE
              </div>
              <div style={{ fontSize: '0.8rem', color: '#fde68a' }}>
                Holding GREEN for {status?.active_manual?.direction || 'Requested Direction'} • Safe transition enforced
              </div>
            </div>
          </div>
          <button className="btn btn-primary" onClick={handleReturnToAutomatic} disabled={manualLoading}>
            <ArrowRightLeft size={16} /> Return to Automatic
          </button>
        </div>
      )}

      {/* Failure Mode Banner */}
      {mode === 'FAILURE' && (
        <div className="glass-panel" style={{
          padding: '14px 20px',
          marginBottom: '16px',
          background: 'rgba(239, 68, 68, 0.2)',
          borderColor: 'rgba(239, 68, 68, 0.6)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <AlertTriangle size={24} color="#f87171" />
            <div>
              <div style={{ fontWeight: 800, color: '#f87171', fontSize: '0.95rem' }}>
                SYSTEM IN FAILURE (DEGRADED) MODE
              </div>
              <div style={{ fontSize: '0.8rem', color: '#fca5a5' }}>
                {controllerStatus === 'OFFLINE'
                  ? 'Controller offline • Set Controller ONLINE to resume automatic control'
                  : 'Controller online • Signals held in ALL_RED safety state until resumed'}
              </div>
            </div>
          </div>
          {controllerStatus === 'ONLINE' && (
            <button
              className="btn btn-primary"
              style={{ fontSize: '0.8rem', padding: '6px 14px' }}
              disabled={manualLoading}
              onClick={handleReturnToAutomatic}
            >
              <RefreshCw size={14} /> Resume Automatic Mode
            </button>
          )}
        </div>
      )}

      {/* ─── Main 3-Column Dashboard Layout ──────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1.1fr 1.3fr', gap: '20px' }}>
        {/* Column 1: Visual Intersection & Signal States */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Intersection Canvas */}
          <div className="glass-panel" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <h2 style={{ fontSize: '1rem', fontWeight: 700, color: '#f8fafc' }}>
                Intersection Visualizer
              </h2>
              <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Live Canvas</span>
            </div>
            <IntersectionVisualizer status={status} />
          </div>

          {/* Desired vs Actual Signals */}
          <div className="glass-panel" style={{ padding: '20px' }}>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: '14px', color: '#f8fafc' }}>
              Physical Signals: Desired vs Actual
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px' }}>
              {['NORTH', 'SOUTH', 'EAST', 'WEST'].map((dir) => {
                const des = desired[dir] || 'RED';
                const act = actual[dir] || 'UNKNOWN';
                const match = des === act;
                return (
                  <div key={dir} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '12px', borderRadius: '10px', textAlign: 'center', border: '1px solid var(--glass-border)' }}>
                    <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#94a3b8', marginBottom: '6px' }}>{dir}</div>
                    <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '8px' }}>
                      <span className={`signal-dot ${des}`} />
                    </div>
                    <div style={{ fontSize: '0.7rem', color: '#e2e8f0', fontWeight: 600 }}>Desired: {des}</div>
                    <div style={{ fontSize: '0.65rem', color: match ? '#34d399' : '#f87171' }}>
                      Actual: {act}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Column 2: Controls, Queues & Countdown */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Countdown & Current Phase */}
          <div className="glass-panel" style={{ padding: '20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase' }}>
                Current Phase Countdown
              </div>
              <div style={{ fontSize: '2rem', fontWeight: 800, color: '#38bdf8', fontFamily: 'JetBrains Mono, monospace' }}>
                {countdown != null ? `${countdown}s` : 'HOLD'}
              </div>
              <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                Step: {status?.current_step || 'RESTING'}
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase' }}>
                Active Phase
              </div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#f8fafc' }}>
                {status?.phase || 'NORTH_SOUTH'}
              </div>
              <span className="badge badge-demo" style={{ marginTop: '4px' }}>
                Speed: 2x
              </span>
            </div>
          </div>

          {/* Manual Control Panel */}
          <div className="glass-panel" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
              <h3 style={{ fontSize: '0.95rem', fontWeight: 700, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Sliders size={18} color="#38bdf8" /> Manual Traffic Controls
              </h3>
              <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Max 3 in queue</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '12px' }}>
              <button
                className="btn btn-glass"
                disabled={manualLoading || mode === 'EMERGENCY' || mode === 'FAILURE'}
                onClick={() => handleManualRequest('NORTH')}
              >
                Request NORTH Green
              </button>
              <button
                className="btn btn-glass"
                disabled={manualLoading || mode === 'EMERGENCY' || mode === 'FAILURE'}
                onClick={() => handleManualRequest('SOUTH')}
              >
                Request SOUTH Green
              </button>
              <button
                className="btn btn-glass"
                disabled={manualLoading || mode === 'EMERGENCY' || mode === 'FAILURE'}
                onClick={() => handleManualRequest('EAST')}
              >
                Request EAST Green
              </button>
              <button
                className="btn btn-glass"
                disabled={manualLoading || mode === 'EMERGENCY' || mode === 'FAILURE'}
                onClick={() => handleManualRequest('WEST')}
              >
                Request WEST Green
              </button>
            </div>

            <button
              className="btn btn-primary"
              style={{ width: '100%' }}
              disabled={manualLoading || mode === 'EMERGENCY' || (mode === 'FAILURE' && controllerStatus !== 'ONLINE')}
              onClick={handleReturnToAutomatic}
            >
              <RefreshCw size={15} /> Return to Automatic
            </button>
          </div>

          {/* Waiting Queues */}
          <div className="glass-panel" style={{ padding: '20px' }}>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: '14px', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Car size={18} color="#34d399" /> Waiting Vehicle Queues
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px' }}>
              {Object.entries(queues).map(([dir, count]) => (
                <div key={dir} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '12px 8px', borderRadius: '10px', textAlign: 'center', border: '1px solid var(--glass-border)' }}>
                  <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 700 }}>{dir}</div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 800, color: count > 0 ? '#38bdf8' : '#64748b', fontFamily: 'JetBrains Mono, monospace' }}>
                    {count}
                  </div>
                </div>
              ))}
            </div>

            {/* Active alerts */}
            {alerts && alerts.length > 0 && (
              <div style={{ marginTop: '16px' }}>
                <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#f59e0b', textTransform: 'uppercase', marginBottom: '6px' }}>
                  System Alerts ({alerts.length})
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {alerts.map((alt, i) => (
                    <div key={i} style={{ fontSize: '0.75rem', padding: '6px 10px', borderRadius: '6px', background: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', border: '1px solid rgba(245, 158, 11, 0.3)' }}>
                      • {alt}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Column 3: Simulation & History */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Simulation Form */}
          <SimulationForm
            junctionId="A"
            onActionSuccess={() => { fetchStatus(); fetchHistory(); }}
            pendingCommands={status?.pending_commands}
          />

          {/* Audit / History Log */}
          <div className="glass-panel" style={{ padding: '20px', flex: 1, maxHeight: '340px', display: 'flex', flexDirection: 'column' }}>
            <h3 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: '12px', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <History size={18} color="#94a3b8" /> Recent Activity & Audit Log
            </h3>
            <div style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', paddingRight: '4px' }}>
              {history && history.length > 0 ? (
                history.map((item) => (
                  <div
                    key={item.id}
                    style={{
                      background: 'rgba(15, 23, 42, 0.5)',
                      padding: '8px 12px',
                      borderRadius: '8px',
                      border: '1px solid var(--glass-border)',
                      fontSize: '0.75rem',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', color: '#94a3b8', marginBottom: '2px' }}>
                      <span style={{ fontWeight: 700, color: '#38bdf8' }}>{item.event_type}</span>
                      <span>{new Date(item.timestamp * 1000).toLocaleTimeString()}</span>
                    </div>
                    <div style={{ color: '#cbd5e1' }}>{item.reason}</div>
                  </div>
                ))
              ) : (
                <div style={{ color: '#64748b', fontSize: '0.8rem', textAlign: 'center', padding: '20px 0' }}>
                  No recent activity logged yet.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
