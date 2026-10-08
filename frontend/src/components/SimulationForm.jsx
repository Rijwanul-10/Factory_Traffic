import React, { useState } from 'react';
import { Send, AlertTriangle, ShieldAlert, Cpu, CheckCircle2, Zap } from 'lucide-react';

export default function SimulationForm({ junctionId = 'A', onActionSuccess, pendingCommands = [] }) {
  const [direction, setDirection] = useState('NORTH');
  const [vehicleType, setVehicleType] = useState('TRUCK');
  const [vehicleId, setVehicleId] = useState('');
  const [clearVehicleId, setClearVehicleId] = useState('');
  const [clearDirection, setClearDirection] = useState('NORTH');
  const [controllerStatus, setControllerStatus] = useState('ONLINE');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState(null);

  const showFeedback = (type, message) => {
    setFeedback({ type, message });
    setTimeout(() => setFeedback(null), 4000);
  };

  // 1. Vehicle Arrival
  const handleArrival = async (overrideType = null, overrideDir = null, overrideId = null) => {
    setIsSubmitting(true);
    const targetType = overrideType || vehicleType;
    const targetDir = overrideDir || direction;
    const targetId = overrideId || vehicleId || `VH-${Math.floor(100 + Math.random() * 900)}`;
    const eventId = `evt-${Date.now().toString().slice(-6)}`;

    try {
      const res = await fetch('/api/sensor-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_id: eventId,
          junction_id: junctionId,
          direction: targetDir,
          event_type: 'VEHICLE_ARRIVED',
          vehicle_id: targetId,
          vehicle_type: targetType,
          sequence_no: Math.floor(Math.random() * 1000) + 1,
          timestamp: new Date().toISOString(),
        }),
      });

      const data = await res.json();
      if (res.ok) {
        showFeedback('success', `Arrival ${targetId} (${targetType}) submitted: ${data.status}`);
        if (!overrideId) setVehicleId('');
        onActionSuccess?.();
      } else {
        showFeedback('error', data.detail || 'Failed to submit arrival');
      }
    } catch (err) {
      showFeedback('error', `Network error: ${err.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 2. Vehicle Clearance
  const handleClearance = async () => {
    if (!clearVehicleId) {
      showFeedback('error', 'Vehicle ID is required to clear');
      return;
    }
    setIsSubmitting(true);
    const eventId = `clr-${Date.now().toString().slice(-6)}`;

    try {
      const res = await fetch('/api/sensor-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_id: eventId,
          junction_id: junctionId,
          direction: clearDirection,
          event_type: 'VEHICLE_CLEARED',
          vehicle_id: clearVehicleId,
          sequence_no: Math.floor(Math.random() * 1000) + 1,
          timestamp: new Date().toISOString(),
        }),
      });

      const data = await res.json();
      if (res.ok) {
        showFeedback('success', `Vehicle ${clearVehicleId} cleared: ${data.status}`);
        setClearVehicleId('');
        onActionSuccess?.();
      } else {
        showFeedback('error', data.detail || 'Failed to submit clearance');
      }
    } catch (err) {
      showFeedback('error', `Network error: ${err.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 3. Controller Status Change
  const handleControllerStatus = async (status) => {
    setIsSubmitting(true);
    try {
      const res = await fetch('/api/controller-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          junction_id: junctionId,
          status: status,
          device_type: 'SIGNAL_CONTROLLER',
        }),
      });

      const data = await res.json();
      if (res.ok) {
        setControllerStatus(status);
        showFeedback('success', `Controller status changed to ${status}`);
        onActionSuccess?.();
      } else {
        showFeedback('error', data.detail || 'Failed to update controller');
      }
    } catch (err) {
      showFeedback('error', `Network error: ${err.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // 4. Controller Manual ACK
  const handleAck = async (cmdId) => {
    setIsSubmitting(true);
    try {
      const res = await fetch('/api/controller-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          junction_id: junctionId,
          status: 'ACK',
          command_id: cmdId,
        }),
      });

      const data = await res.json();
      if (res.ok) {
        showFeedback('success', `Command ${cmdId} acknowledged!`);
        onActionSuccess?.();
      } else {
        showFeedback('error', data.detail || 'Failed to acknowledge');
      }
    } catch (err) {
      showFeedback('error', `Network error: ${err.message}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Preset Demo Scenarios
  const runPresetScenario = async (scenarioNumber) => {
    switch (scenarioNumber) {
      case 1: // Normal Traffic
        await handleArrival('EMPLOYEE_VEHICLE', 'NORTH', 'EMP-101');
        await handleArrival('FORKLIFT', 'SOUTH', 'FORK-201');
        break;
      case 2: // Priority Traffic
        await handleArrival('TRUCK', 'EAST', 'TRUCK-777');
        break;
      case 3: // Emergency Preemption
        await handleArrival('EMERGENCY', 'EAST', 'AMBULANCE-911');
        break;
      case 4: // Stale Emergency
        await handleArrival('EMERGENCY', 'WEST', 'STALE-AMB');
        showFeedback('success', 'Scenario 4: Dispatched emergency vehicle STALE-AMB (auto-drops after 60s nominal if not cleared)');
        break;
      case 5: // Duplicate Event
        const dupId = `VH-DUP-${Math.floor(Math.random() * 900)}`;
        const fixedEvtId = `evt-fixed-${Date.now()}`;
        // Submit first
        await fetch('/api/sensor-events', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            event_id: fixedEvtId,
            junction_id: junctionId,
            direction: 'NORTH',
            event_type: 'VEHICLE_ARRIVED',
            vehicle_id: dupId,
            vehicle_type: 'FORKLIFT',
            sequence_no: 1,
            timestamp: new Date().toISOString(),
          }),
        });
        // Submit duplicate immediately
        await fetch('/api/sensor-events', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            event_id: fixedEvtId,
            junction_id: junctionId,
            direction: 'NORTH',
            event_type: 'VEHICLE_ARRIVED',
            vehicle_id: dupId,
            vehicle_type: 'FORKLIFT',
            sequence_no: 1,
            timestamp: new Date().toISOString(),
          }),
        });
        showFeedback('success', 'Scenario 5: Submitted exact duplicate event_id!');
        onActionSuccess?.();
        break;
      case 6: // Starvation Override
        await handleArrival('TRUCK', 'EAST', 'STARVE-TRK');
        await handleArrival('EMPLOYEE_VEHICLE', 'EAST', 'STARVE-EMP');
        showFeedback('success', 'Scenario 6: Injected conflicting queue to demonstrate starvation protection');
        break;
      case 7: // Controller Offline
        await handleControllerStatus('OFFLINE');
        break;
      case 8: // Controller Reconnect & Safe Recovery
        await handleControllerStatus('ONLINE');
        showFeedback('success', 'Scenario 8: Controller reconnected; safe recovery initiated');
        break;
      case 9: // Concurrent Burst
        const t0 = handleArrival('TRUCK', 'NORTH', 'BURST-TRUCK');
        const t4 = handleArrival('EMERGENCY', 'EAST', 'BURST-EMG');
        const t8 = fetch(`/api/junctions/${junctionId}/commands`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: 'MANUAL_GREEN_REQUEST', direction: 'WEST' }),
        });
        await Promise.all([t0, t4, t8]);
        showFeedback('success', 'Scenario 9: Dispatched concurrent events!');
        onActionSuccess?.();
        break;
      default:
        break;
    }
  };

  return (
    <div className="glass-panel" style={{ padding: '20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <h3 style={{ fontSize: '1.1rem', fontWeight: 700, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Zap size={20} color="#38bdf8" />
          Hardware & Sensor Simulator
        </h3>
        <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Real-time Injection</span>
      </div>

      {feedback && (
        <div style={{
          padding: '10px 14px',
          borderRadius: '8px',
          marginBottom: '14px',
          fontSize: '0.85rem',
          fontWeight: 600,
          background: feedback.type === 'success' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
          color: feedback.type === 'success' ? '#34d399' : '#f87171',
          border: `1px solid ${feedback.type === 'success' ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'}`,
        }}>
          {feedback.message}
        </div>
      )}

      {/* Quick Scenario Buttons */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 600, textTransform: 'uppercase', marginBottom: '8px' }}>
          Quick Demo Scenarios
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '8px' }}>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px' }} onClick={() => runPresetScenario(1)}>
            1. Normal Flow
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px' }} onClick={() => runPresetScenario(2)}>
            2. Priority Truck
          </button>
          <button className="btn btn-danger" style={{ fontSize: '0.75rem', padding: '6px 8px' }} onClick={() => runPresetScenario(3)}>
            <ShieldAlert size={14} /> 3. Emergency
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px', color: '#fbbf24' }} onClick={() => runPresetScenario(4)}>
            4. Stale EMG
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px' }} onClick={() => runPresetScenario(5)}>
            5. Duplicate Evt
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px', color: '#a78bfa' }} onClick={() => runPresetScenario(6)}>
            6. Starvation
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px', color: '#f87171' }} onClick={() => runPresetScenario(7)}>
            7. Offline Fail
          </button>
          <button className="btn btn-glass" style={{ fontSize: '0.75rem', padding: '6px 8px', color: '#34d399' }} onClick={() => runPresetScenario(8)}>
            8. Recover Online
          </button>
          <button className="btn btn-primary" style={{ fontSize: '0.75rem', padding: '6px 8px' }} onClick={() => runPresetScenario(9)}>
            9. Concurrent
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '16px' }}>
        {/* Vehicle Arrival Section */}
        <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '14px', borderRadius: '12px', border: '1px solid var(--glass-border)' }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 700, marginBottom: '10px', color: '#38bdf8' }}>
            + Vehicle Arrival
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div>
              <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>Direction</label>
              <select className="input-field" value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option value="NORTH">NORTH</option>
                <option value="SOUTH">SOUTH</option>
                <option value="EAST">EAST</option>
                <option value="WEST">WEST</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>Vehicle Type</label>
              <select className="input-field" value={vehicleType} onChange={(e) => setVehicleType(e.target.value)}>
                <option value="TRUCK">TRUCK (Priority 3)</option>
                <option value="FORKLIFT">FORKLIFT (Priority 2)</option>
                <option value="EMPLOYEE_VEHICLE">EMPLOYEE (Priority 1)</option>
                <option value="EMERGENCY">EMERGENCY (Preemption)</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>Vehicle ID (Optional)</label>
              <input
                className="input-field"
                type="text"
                placeholder="e.g. VH-501"
                value={vehicleId}
                onChange={(e) => setVehicleId(e.target.value)}
              />
            </div>

            <button
              className="btn btn-primary"
              style={{ marginTop: '4px', width: '100%' }}
              disabled={isSubmitting}
              onClick={() => handleArrival()}
            >
              <Send size={15} /> Submit Arrival
            </button>
          </div>
        </div>

        {/* Vehicle Clearance Section */}
        <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '14px', borderRadius: '12px', border: '1px solid var(--glass-border)' }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 700, marginBottom: '10px', color: '#34d399' }}>
            - Vehicle Clearance
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div>
              <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>Direction</label>
              <select className="input-field" value={clearDirection} onChange={(e) => setClearDirection(e.target.value)}>
                <option value="NORTH">NORTH</option>
                <option value="SOUTH">SOUTH</option>
                <option value="EAST">EAST</option>
                <option value="WEST">WEST</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.75rem', color: '#94a3b8', display: 'block', marginBottom: '4px' }}>Vehicle ID to Clear</label>
              <input
                className="input-field"
                type="text"
                placeholder="e.g. VH-501"
                value={clearVehicleId}
                onChange={(e) => setClearVehicleId(e.target.value)}
              />
            </div>

            <div style={{ height: '49px' }}></div>

            <button
              className="btn btn-glass"
              style={{ marginTop: '4px', width: '100%', borderColor: 'rgba(16, 185, 129, 0.4)', color: '#34d399' }}
              disabled={isSubmitting || !clearVehicleId}
              onClick={handleClearance}
            >
              <CheckCircle2 size={15} /> Submit Clearance
            </button>
          </div>
        </div>
      </div>

      {/* Controller Simulator Controls */}
      <div style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '14px', borderRadius: '12px', border: '1px solid var(--glass-border)' }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 700, marginBottom: '10px', color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Cpu size={16} /> Signal Controller State & Manual ACKs
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <button
            className="btn btn-glass"
            style={{ fontSize: '0.8rem', padding: '6px 12px', color: '#34d399' }}
            onClick={() => handleControllerStatus('ONLINE')}
          >
            Set ONLINE
          </button>
          <button
            className="btn btn-glass"
            style={{ fontSize: '0.8rem', padding: '6px 12px', color: '#f87171' }}
            onClick={() => handleControllerStatus('OFFLINE')}
          >
            Set OFFLINE
          </button>
          <button
            className="btn btn-glass"
            style={{ fontSize: '0.8rem', padding: '6px 12px', color: '#fbbf24' }}
            onClick={() => handleControllerStatus('DEGRADED')}
          >
            Set DEGRADED
          </button>

          {pendingCommands && pendingCommands.length > 0 && (
            <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
              <span style={{ fontSize: '0.75rem', color: '#f59e0b', fontWeight: 600 }}>
                Pending ACK:
              </span>
              {pendingCommands.slice(0, 2).map((c) => (
                <button
                  key={c.command_id}
                  className="btn btn-glass"
                  style={{ fontSize: '0.75rem', padding: '4px 8px', color: '#38bdf8' }}
                  onClick={() => handleAck(c.command_id)}
                >
                  ACK {c.command_id}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
