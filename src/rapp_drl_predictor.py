#!/usr/bin/env python3
"""
GreenRAN DRL Predictor (Tempo Real)
=================================
DRL-based predictor usando SBiLSTM + A3C.

Características:
  - SBiLSTM: Predição de CVaR (latência)
  - A3C: Decisão ALLOWED/BLOCKED/CONDITIONAL
  - Interface O-RAN compatível

Usage:
    from rapp_drl_predictor import DRLPredictor
    predictor = DRLPredictor()
    decision = predictor.predict(metrics_dict)
"""

import os
import sys
import json
import torch
import numpy as np
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DRL_DIR = os.path.join(PROJECT_DIR, 'drlexp')
MODEL_DIR = os.path.join(DRL_DIR, 'models')

CVAR_BLOCKED = 80
CVAR_WARNING = 60
CVAR_GREEN = 50
SEQUENCE_LEN = 100
THROUGHPUT_MODEL_CAP_MBPS = 300

FEATURE_ORDER = [
    'cvar_ms',
    'cvar_trend',
    'cvar_acceleration',
    'latency_p95_ms',
    'jitter_ms',
    'packet_loss_pct',
    'throughput_mbps',
    'active_ues',
    'active_cameras',
    'critical_ues',
    'camera_ratio',
    'critical_ue_ratio',
    'allocated_rbs',
    'current_power',
    'power_budget',
    'hour_sin',
    'hour_cos',
    'variance_ms2',
]
FEATURE_INDEX = {name: idx for idx, name in enumerate(FEATURE_ORDER)}

FEATURE_LIMITS = {
    'cvar_ms': (0, 250),
    'cvar_trend': (-100, 100),
    'cvar_acceleration': (-100, 100),
    'latency_p95_ms': (0, 250),
    'jitter_ms': (0, 50),
    'packet_loss_pct': (0, 100),
    # O modelo foi treinado em faixas menores; throughput global muito alto
    # vira out-of-distribution e fazia a SBiLSTM superestimar CVaR.
    'throughput_mbps': (0, THROUGHPUT_MODEL_CAP_MBPS),
    'active_ues': (0, 200),
    'active_cameras': (0, 50),
    'critical_ues': (0, 200),
    'camera_ratio': (0, 1),
    'critical_ue_ratio': (0, 1),
    'allocated_rbs': (0, 2000),
    'current_power': (0, 100),
    'power_budget': (0, 100),
    'hour_sin': (-1, 1),
    'hour_cos': (-1, 1),
    'variance_ms2': (0, 10000),
}
MIN_HISTORY_FOR_STABLE_SEQUENCE = 8
EARLY_CVAR_BLEND_WEIGHT = 0.35
PREDICTION_SMOOTH_WINDOW = 5
DECISION_STABILITY_WINDOW = 3
DECISION_HYSTERESIS_MS = 5.0
RECOVERY_RELEASE_MARGIN_MS = 6.0
TRACE_ENV_FLAG = "GREENRAN_DRL_TRACE"
TRACE_ENV_FILE = "GREENRAN_DRL_TRACE_FILE"
RISK_PROMOTION_CONDITIONAL_MS = 52.0
RISK_PROMOTION_BLOCKED_MS = 76.0
RECOVERY_DEMOTION_ALLOWED_MS = 46.0
RISK_SCORE_CONDITIONAL = 0.48
RISK_SCORE_BLOCKED = 0.78
RISK_SCORE_RELEASE = 0.34


def _bounded_float(value, lower, upper):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = lower
    if not np.isfinite(value):
        value = lower
    return float(np.clip(value, lower, upper))


def state_to_features(state_dict):
    """Convert state dict to the 18-feature vector expected by the DRL models."""
    defaults = {
        'cvar_ms': 50,
        'cvar_trend': 0,
        'cvar_acceleration': 0,
        'latency_p95_ms': 60,
        'jitter_ms': 2,
        'packet_loss_pct': 0.1,
        'throughput_mbps': 100,
        'active_ues': 20,
        'active_cameras': 3,
        'critical_ues': 0,
        'camera_ratio': 0.15,
        'critical_ue_ratio': 0,
        'allocated_rbs': 200,
        'current_power': 20,
        'power_budget': 30,
        'hour_sin': 0,
        'hour_cos': 1,
        'variance_ms2': 10,
    }
    features = []
    for name in FEATURE_ORDER:
        lower, upper = FEATURE_LIMITS[name]
        features.append(_bounded_float(state_dict.get(name, defaults[name]), lower, upper))
    return np.array(features, dtype=np.float32)


def load_sbilstm(model_path):
    """Load SBiLSTM model."""
    sys.path.insert(0, os.path.join(DRL_DIR, 'src'))
    from drl.models.sbilstm import SBiLSTM
    
    model = SBiLSTM(
        input_size=18,
        hidden_size_1=128,
        hidden_size_2=64,
        num_layers=2,
        dropout=0.2,
        output_size=1
    )
    model.load_state_dict(torch.load(model_path, map_location='cpu'))
    model.eval()
    return model


def load_a3c(actor_path, critic_path):
    """Load A3C Actor and Critic models."""
    sys.path.insert(0, os.path.join(DRL_DIR, 'src'))
    from drl.models.actor import ActorNetwork
    from drl.models.critic import CriticNetwork
    
    actor = ActorNetwork(state_size=18, num_actions=9)
    critic = CriticNetwork(state_size=18)
    
    actor.load_state_dict(torch.load(actor_path, map_location='cpu'))
    critic.load_state_dict(torch.load(critic_path, map_location='cpu'))
    
    actor.eval()
    critic.eval()
    return actor, critic


def state_to_tensor(state_dict):
    """Convert state dict to tensor."""
    return torch.FloatTensor(state_to_features(state_dict)).unsqueeze(0).unsqueeze(0)


def state_to_sequence(state_dict, seq_len=SEQUENCE_LEN, history=None):
    """Convert state dict to sequence tensor for SBiLSTM."""
    if history:
        seq = np.array(history[-seq_len:], dtype=np.float32)
    else:
        seq = np.array([state_to_features(state_dict)], dtype=np.float32)

    if len(seq) < seq_len:
        pad = np.repeat(seq[:1], seq_len - len(seq), axis=0)
        seq = np.vstack([pad, seq])

    return torch.FloatTensor(seq).unsqueeze(0)  # (1, seq_len, 18)


class DRLPredictor:
    """DRL prediction module."""
    
    def __init__(self, model_dir=MODEL_DIR):
        self.model_dir = model_dir
        self.sbilstm = None
        self.actor = None
        self.critic = None
        self.loaded = False
        self.sequence_len = SEQUENCE_LEN
        self._state_history = []
        self._raw_state_history = []
        self._prediction_history = []
        self.trace_enabled = os.environ.get(TRACE_ENV_FLAG, "0").lower() in {"1", "true", "yes", "on"}
        self.trace_file = os.environ.get(TRACE_ENV_FILE, "/tmp/drl_predictor_trace.jsonl")
        
    def load_models(self):
        """Load all DRL models."""
        if self.loaded:
            return
        
        sbilstm_path = os.path.join(self.model_dir, 'sbilstm', 'best_model.pt')
        actor_path = os.path.join(self.model_dir, 'a3c', 'actor_v7.pt')
        critic_path = os.path.join(self.model_dir, 'a3c', 'critic_v7.pt')
        
        print(f"[DRL] Loading SBiLSTM from {sbilstm_path}")
        self.sbilstm = load_sbilstm(sbilstm_path)
        
        print(f"[DRL] Loading A3C from {actor_path}")
        self.actor, self.critic = load_a3c(actor_path, critic_path)
        
        self.loaded = True
        print("[DRL] All models loaded successfully")
        
    def reset_history(self):
        """Reset temporal state when a new simulation starts."""
        self._state_history = []
        self._raw_state_history = []
        self._prediction_history = []

    @staticmethod
    def _decision_rank(decision):
        return {'ALLOWED': 0, 'CONDITIONAL': 1, 'BLOCKED': 2}.get(decision, 1)

    @staticmethod
    def _rank_to_decision(rank):
        return {0: 'ALLOWED', 1: 'CONDITIONAL', 2: 'BLOCKED'}.get(rank, 'CONDITIONAL')

    def _stabilize_output(self, predicted_cvar_ms, final_decision, confidence):
        """Reduce decision flapping using recent predictor outputs only."""
        recent = self._prediction_history[-PREDICTION_SMOOTH_WINDOW:]
        if not recent:
            return predicted_cvar_ms, final_decision, confidence, 1.0, False

        recent_cvars = [float(item['predicted_cvar_ms']) for item in recent]
        recent_decisions = [item['final_decision'] for item in recent]
        median_recent = float(np.median(recent_cvars))
        smoothed_cvar = (0.7 * float(predicted_cvar_ms)) + (0.3 * median_recent)
        current_state = self._raw_state_history[-1] if self._raw_state_history else {}
        current_cvar = _bounded_float(current_state.get('cvar_ms', smoothed_cvar), 0, 500)
        current_trend = _bounded_float(current_state.get('cvar_trend', 0), -500, 500)
        if current_trend < 0:
            # Recovery phases should release faster instead of keeping an overly pessimistic tail.
            smoothed_cvar = min(smoothed_cvar, current_cvar + RECOVERY_RELEASE_MARGIN_MS)

        previous_decision = recent_decisions[-1]
        stable_recent = (
            len(recent_decisions) >= DECISION_STABILITY_WINDOW
            and len(set(recent_decisions[-DECISION_STABILITY_WINDOW:])) == 1
        )

        stable_decision = final_decision
        hysteresis_applied = False
        if previous_decision != final_decision:
            near_warning = abs(smoothed_cvar - CVAR_WARNING) <= DECISION_HYSTERESIS_MS
            near_blocked = abs(smoothed_cvar - CVAR_BLOCKED) <= DECISION_HYSTERESIS_MS
            if stable_recent and confidence < 0.60 and current_trend >= 0 and (near_warning or near_blocked):
                stable_decision = previous_decision
                hysteresis_applied = True

        stability_factor = 1.0
        if stable_recent and stable_decision == previous_decision:
            stability_factor = 1.10
        elif previous_decision != final_decision:
            stability_factor = 0.90

        adjusted_confidence = float(np.clip(confidence * stability_factor, 0.0, 1.0))
        return smoothed_cvar, stable_decision, adjusted_confidence, stability_factor, hysteresis_applied

    def _risk_adjust_actor_decision(self, state_dict, predicted_cvar_ms, actor_decision, actor_confidence):
        """Promote the actor decision when the current risk state is already deteriorating."""
        current_cvar = _bounded_float(state_dict.get('cvar_ms', 0), 0, 500)
        p95 = _bounded_float(state_dict.get('latency_p95_ms', 0), 0, 500)
        trend = _bounded_float(state_dict.get('cvar_trend', 0), -500, 500)
        critical_ues = _bounded_float(state_dict.get('critical_ues', 0), 0, 200)
        packet_loss = _bounded_float(state_dict.get('packet_loss_pct', 0), 0, 100)
        baseline = max(current_cvar, p95, float(predicted_cvar_ms))

        adjusted_decision = actor_decision
        adjustment_reason = ""

        if (
            actor_decision != 'BLOCKED'
            and baseline >= RISK_PROMOTION_BLOCKED_MS
            and trend > 0
            and (critical_ues > 0 or packet_loss >= 1.0)
            and actor_confidence < 0.55
        ):
            adjusted_decision = 'BLOCKED'
            adjustment_reason = 'RISK_PROMOTION_BLOCKED'
        elif (
            actor_decision == 'ALLOWED'
            and baseline >= RISK_PROMOTION_CONDITIONAL_MS
            and (trend > 0 or critical_ues > 0 or p95 >= 70)
            and actor_confidence < 0.60
        ):
            adjusted_decision = 'CONDITIONAL'
            adjustment_reason = 'RISK_PROMOTION_CONDITIONAL'
        elif (
            actor_decision in {'CONDITIONAL', 'BLOCKED'}
            and current_cvar <= RECOVERY_DEMOTION_ALLOWED_MS
            and p95 < 65
            and trend < 0
            and critical_ues == 0
            and actor_confidence < 0.65
        ):
            adjusted_decision = 'ALLOWED'
            adjustment_reason = 'RECOVERY_DEMOTION_ALLOWED'

        return adjusted_decision, adjustment_reason

    def _compute_risk_score(self, state_dict, predicted_cvar_ms):
        """Continuous risk score derived from current and predicted QoS state."""
        current_cvar = _bounded_float(state_dict.get('cvar_ms', 0), 0, 500)
        p95 = _bounded_float(state_dict.get('latency_p95_ms', 0), 0, 500)
        trend = _bounded_float(state_dict.get('cvar_trend', 0), -500, 500)
        packet_loss = _bounded_float(state_dict.get('packet_loss_pct', 0), 0, 100)
        critical_ratio = _bounded_float(state_dict.get('critical_ue_ratio', 0), 0, 1)
        critical_ues = _bounded_float(state_dict.get('critical_ues', 0), 0, 200)
        variance = _bounded_float(state_dict.get('variance_ms2', 0), 0, 10000)

        predicted_norm = np.clip(float(predicted_cvar_ms) / CVAR_BLOCKED, 0.0, 1.4)
        current_norm = np.clip(current_cvar / CVAR_BLOCKED, 0.0, 1.4)
        p95_norm = np.clip(p95 / CVAR_BLOCKED, 0.0, 1.4)
        trend_norm = np.clip(max(trend, 0.0) / 20.0, 0.0, 1.0)
        packet_norm = np.clip(packet_loss / 5.0, 0.0, 1.0)
        critical_norm = np.clip(max(critical_ratio, critical_ues / 10.0), 0.0, 1.0)
        variance_norm = np.clip(variance / 400.0, 0.0, 1.0)

        risk_score = (
            0.34 * predicted_norm
            + 0.22 * current_norm
            + 0.16 * p95_norm
            + 0.10 * trend_norm
            + 0.08 * packet_norm
            + 0.07 * critical_norm
            + 0.03 * variance_norm
        )
        risk_score = float(np.clip(risk_score, 0.0, 1.0))

        if risk_score >= RISK_SCORE_BLOCKED:
            risk_band = 'blocked'
        elif risk_score >= RISK_SCORE_CONDITIONAL:
            risk_band = 'conditional'
        else:
            risk_band = 'allowed'

        return risk_score, risk_band

    def _store_prediction(self, predicted_cvar_ms, final_decision, confidence, policy_action):
        self._prediction_history.append(
            {
                'predicted_cvar_ms': float(predicted_cvar_ms),
                'final_decision': final_decision,
                'confidence': float(confidence),
                'policy_action': policy_action,
            }
        )
        if len(self._prediction_history) > self.sequence_len:
            self._prediction_history = self._prediction_history[-self.sequence_len:]

    def _trace_prediction(self, state_dict, result):
        if not self.trace_enabled:
            return
        try:
            payload = {
                "timestamp": datetime.now().isoformat(),
                "input": {
                    "cvar_ms": float(state_dict.get("cvar_ms", 0) or 0),
                    "cvar_trend": float(state_dict.get("cvar_trend", 0) or 0),
                    "latency_p95_ms": float(state_dict.get("latency_p95_ms", 0) or 0),
                    "throughput_mbps": float(state_dict.get("throughput_mbps", 0) or 0),
                    "active_ues": int(state_dict.get("active_ues", 0) or 0),
                    "active_cameras": int(state_dict.get("active_cameras", 0) or 0),
                    "critical_ues": int(state_dict.get("critical_ues", 0) or 0),
                },
                "output": {
                    "predicted_cvar_ms": result.get("predicted_cvar_ms"),
                    "raw_predicted_cvar_ms": result.get("raw_predicted_cvar_ms"),
                    "a3c_decision_raw": result.get("a3c_decision_raw"),
                    "a3c_decision": result.get("a3c_decision"),
                    "actor_adjustment_reason": result.get("actor_adjustment_reason"),
                    "decision_probabilities": result.get("decision_probabilities"),
                    "final_decision": result.get("final_decision"),
                    "power": result.get("power"),
                    "policy_action": result.get("policy_action"),
                    "confidence": result.get("confidence"),
                    "actor_confidence": result.get("actor_confidence"),
                    "actor_entropy": result.get("actor_entropy"),
                    "actor_margin": result.get("actor_margin"),
                    "critic_value": result.get("critic_value"),
                    "warmup_factor": result.get("warmup_factor"),
                    "stability_factor": result.get("stability_factor"),
                    "risk_score": result.get("risk_score"),
                    "risk_band": result.get("risk_band"),
                    "hysteresis_applied": result.get("hysteresis_applied"),
                    "calibrated": result.get("calibrated"),
                    "calibration_reason": result.get("calibration_reason"),
                    "state_sequence_len": result.get("state_sequence_len"),
                },
            }
            with open(self.trace_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _enrich_state(self, state_dict):
        """Derive missing temporal/context features without depending on the rApp."""
        enriched = dict(state_dict or {})
        now = datetime.now()
        hour_angle = (2.0 * np.pi * now.hour) / 24.0
        enriched['hour_sin'] = float(np.sin(hour_angle))
        enriched['hour_cos'] = float(np.cos(hour_angle))

        active_ues = max(_bounded_float(enriched.get('active_ues', 20), 0, 200), 1.0)
        active_cameras = _bounded_float(enriched.get('active_cameras', 3), 0, 50)
        critical_ues = _bounded_float(enriched.get('critical_ues', 0), 0, 200)
        enriched['camera_ratio'] = float(active_cameras / active_ues)
        enriched['critical_ue_ratio'] = float(critical_ues / active_ues)

        current_cvar = _bounded_float(enriched.get('cvar_ms', 0), 0, 500)
        previous_state = self._raw_state_history[-1] if self._raw_state_history else None
        previous_previous_state = self._raw_state_history[-2] if len(self._raw_state_history) >= 2 else None

        if previous_state is not None:
            previous_cvar = _bounded_float(previous_state.get('cvar_ms', 0), 0, 500)
            derived_trend = current_cvar - previous_cvar
            incoming_trend = enriched.get('cvar_trend')
            if incoming_trend in (None, 0, 0.0):
                enriched['cvar_trend'] = derived_trend

            previous_trend = _bounded_float(previous_state.get('cvar_trend', derived_trend), -500, 500)
            derived_acc = derived_trend - previous_trend
            incoming_acc = enriched.get('cvar_acceleration')
            if incoming_acc in (None, 0, 0.0):
                enriched['cvar_acceleration'] = derived_acc
        else:
            enriched['cvar_trend'] = _bounded_float(enriched.get('cvar_trend', 0), -500, 500)
            enriched['cvar_acceleration'] = _bounded_float(enriched.get('cvar_acceleration', 0), -500, 500)

        if previous_state is None and previous_previous_state is None:
            variance_ms2 = _bounded_float(enriched.get('variance_ms2', 10), 0, 10000)
        else:
            recent_cvars = [
                _bounded_float(item.get('cvar_ms', 0), 0, 500)
                for item in [*self._raw_state_history[-4:], enriched]
            ]
            variance_ms2 = float(np.var(recent_cvars)) if recent_cvars else _bounded_float(enriched.get('variance_ms2', 10), 0, 10000)
        enriched['variance_ms2'] = variance_ms2

        return enriched

    def _record_state(self, state_dict):
        enriched_state = self._enrich_state(state_dict)
        features = state_to_features(enriched_state)
        self._state_history.append(features)
        self._raw_state_history.append(enriched_state)
        if len(self._state_history) > self.sequence_len:
            self._state_history = self._state_history[-self.sequence_len:]
            self._raw_state_history = self._raw_state_history[-self.sequence_len:]
        return features

    def predict_cvar(self, state_dict, use_recorded_history=True):
        """Predict CVaR using SBiLSTM."""
        if not self.loaded:
            self.load_models()
            
        history = self._state_history if use_recorded_history else None
        state = state_to_sequence(state_dict, seq_len=self.sequence_len, history=history)
        
        with torch.no_grad():
            prediction = self.sbilstm.predict(state)
        predicted_cvar = float(prediction.item())
        if use_recorded_history and self._raw_state_history:
            warmup = min(len(self._raw_state_history) / float(MIN_HISTORY_FOR_STABLE_SEQUENCE), 1.0)
            current_cvar = _bounded_float(self._raw_state_history[-1].get('cvar_ms', 0), 0, 500)
            if warmup < 1.0:
                blend_weight = EARLY_CVAR_BLEND_WEIGHT + ((1.0 - EARLY_CVAR_BLEND_WEIGHT) * warmup)
                predicted_cvar = (predicted_cvar * blend_weight) + (current_cvar * (1.0 - blend_weight))
        return predicted_cvar
    
    def predict_action(self, state_dict, features=None):
        """Predict action using A3C."""
        if not self.loaded:
            self.load_models()
            
        if features is None:
            state = state_to_tensor(state_dict)
        else:
            state = torch.FloatTensor(features).unsqueeze(0).unsqueeze(0)
        state_flat = state.view(state.shape[0], -1)
        
        with torch.no_grad():
            action_probs = self.actor.forward_inference(state_flat)
            state_value = self.critic.forward_inference(state_flat)
            
        decisions = ['ALLOWED', 'CONDITIONAL', 'BLOCKED']
        powers = ['REDUCE', 'MAINTAIN', 'INCREASE']
        
        probs = action_probs.squeeze().tolist()
        decision_probs = [
            float(sum(probs[0:3])),
            float(sum(probs[3:6])),
            float(sum(probs[6:9])),
        ]
        decision = int(np.argmax(decision_probs))
        power_offset = int(np.argmax(probs[decision * 3:(decision + 1) * 3]))
        action = (decision * 3) + power_offset
        power = power_offset
        sorted_probs = sorted((float(p) for p in probs), reverse=True)
        top1 = sorted_probs[0] if sorted_probs else 0.0
        top2 = sorted_probs[1] if len(sorted_probs) > 1 else 0.0
        margin = max(top1 - top2, 0.0)
        entropy = 0.0
        if probs:
            entropy = float(-sum(float(p) * np.log(float(p) + 1e-8) for p in probs) / np.log(len(probs)))
        value_conf = float(np.tanh(max(float(state_value.item()), 0.0) / 5.0))
        warmup = min(len(self._raw_state_history) / float(MIN_HISTORY_FOR_STABLE_SEQUENCE), 1.0)
        confidence = top1 * (0.55 + 0.25 * margin + 0.20 * value_conf) * (1.0 - 0.35 * entropy) * (0.60 + 0.40 * warmup)
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return {
            'action': action,
            'decision': decisions[decision],
            'power': powers[power],
            'probabilities': probs,
            'decision_probabilities': decision_probs,
            'confidence': confidence,
            'entropy': entropy,
            'margin': margin,
            'critic_value': float(state_value.item()),
            'warmup_factor': warmup,
        }
    
    def _calibrate_prediction(self, state_dict, raw_predicted_cvar, actor_decision, actor_confidence, warmup_factor=1.0):
        """Align raw DRL output with the current GreenRAN scenario envelope."""
        current_cvar = _bounded_float(state_dict.get('cvar_ms', 0), 0, 500)
        p95 = _bounded_float(state_dict.get('latency_p95_ms', 0), 0, 500)
        trend = _bounded_float(state_dict.get('cvar_trend', 0), -500, 500)
        packet_loss = _bounded_float(state_dict.get('packet_loss_pct', 0), 0, 100)
        critical_ues = _bounded_float(state_dict.get('critical_ues', 0), 0, 200)
        baseline = max(current_cvar, p95)

        healthy_network = (
            baseline < 40
            and p95 < 60
            and trend <= 1
            and packet_loss < 1
            and critical_ues == 0
        )

        calibrated_cvar = float(raw_predicted_cvar)
        calibration_reason = ''
        calibrated = False

        if healthy_network and raw_predicted_cvar > CVAR_WARNING:
            calibrated_cvar = min(raw_predicted_cvar, baseline + 8)
            calibration_reason = 'HEALTHY_NETWORK_RAW_DRL_SUPERESTIMATE'
            calibrated = True
        elif baseline < CVAR_WARNING and trend <= 0 and raw_predicted_cvar > baseline + 40:
            calibrated_cvar = min(raw_predicted_cvar, baseline + 20)
            calibration_reason = 'RAW_DRL_OUTLIER_VS_REAL_KPI'
            calibrated = True

        risk_score, risk_band = self._compute_risk_score(state_dict, calibrated_cvar)

        if calibrated_cvar >= CVAR_BLOCKED or risk_score >= RISK_SCORE_BLOCKED:
            final_decision = 'BLOCKED'
        elif calibrated_cvar >= CVAR_WARNING or risk_score >= RISK_SCORE_CONDITIONAL:
            final_decision = 'CONDITIONAL'
        elif (
            actor_decision == 'CONDITIONAL'
            and warmup_factor < 0.35
            and baseline < 35
            and p95 < 45
            and packet_loss < 1
            and critical_ues == 0
            and risk_score <= 0.24
        ):
            final_decision = 'ALLOWED'
            calibration_reason = calibration_reason or 'HEALTHY_WARMUP_CONDITIONAL_RELAX'
            calibrated = True
        elif healthy_network and risk_score <= RISK_SCORE_RELEASE:
            final_decision = 'ALLOWED'
        elif (
            actor_decision == 'CONDITIONAL'
            and current_cvar <= 45
            and p95 < 55
            and trend < 0
            and critical_ues == 0
            and risk_score < (RISK_SCORE_CONDITIONAL + 0.02)
        ):
            final_decision = 'ALLOWED'
        elif actor_decision == 'BLOCKED' and calibrated_cvar < CVAR_WARNING and risk_score < (RISK_SCORE_BLOCKED - 0.08):
            final_decision = 'CONDITIONAL'
            calibration_reason = calibration_reason or 'A3C_BLOCKED_WITH_LOW_CALIBRATED_CVAR'
            calibrated = True
        else:
            final_decision = actor_decision

        confidence = float(actor_confidence)
        if calibrated:
            confidence = min(confidence, 0.55)
        if final_decision == 'ALLOWED' and risk_score < RISK_SCORE_RELEASE:
            confidence = float(np.clip(confidence + 0.08, 0.0, 1.0))
        elif final_decision == 'BLOCKED' and risk_score >= RISK_SCORE_BLOCKED:
            confidence = float(np.clip(confidence + 0.05, 0.0, 1.0))

        return {
            'predicted_cvar_ms': calibrated_cvar,
            'final_decision': final_decision,
            'confidence': confidence,
            'calibrated': calibrated,
            'calibration_reason': calibration_reason,
            'risk_score': risk_score,
            'risk_band': risk_band,
        }

    def _select_policy_action(self, final_decision, power, predicted_cvar, current_cvar, current_trend, confidence, risk_score):
        """Translate DRL control output into an energy command profile."""
        if final_decision == 'BLOCKED':
            return 'FULL_POWER', 100, 'protect_sla'

        if final_decision == 'CONDITIONAL':
            if power == 'INCREASE' or risk_score >= 0.85:
                return 'FULL_POWER', 100, 'conditional_protect'
            if risk_score <= 0.40 and current_trend < 0 and predicted_cvar < 56 and confidence >= 0.20:
                return 'POWER_DOWN', 50, 'conditional_relaxed_reduce'
            if risk_score <= 0.50 and current_trend < 0 and predicted_cvar < 58 and confidence >= 0.18:
                return 'POWER_DOWN', 50, 'conditional_recovery_reduce'
            return 'CONDITIONAL_REDUCE', 70, 'conditional_reduce'

        if power == 'REDUCE':
            if risk_score <= 0.18 and predicted_cvar < 18 and current_cvar < 22 and current_trend <= 0:
                return 'POWER_DOWN_ECO', 25, 'eco_reduce'
            if risk_score <= 0.42 or (current_cvar < 48 and current_trend <= 0):
                return 'POWER_DOWN', 50, 'medium_reduce'
            return 'CONDITIONAL_REDUCE', 70, 'cautious_reduce'

        if power == 'MAINTAIN':
            if risk_score <= 0.38 and current_trend <= 0:
                return 'POWER_DOWN', 50, 'maintain_medium_reduce'
            return 'CONDITIONAL_REDUCE', 70, 'maintain_cautious'

        return 'CONDITIONAL_REDUCE', 70, 'increase_requested_cautious'

    def predict(self, state_dict):
        """Full prediction combining SBiLSTM + A3C."""
        features = self._record_state(state_dict)
        raw_predicted_cvar = self.predict_cvar(state_dict, use_recorded_history=True)
        action_result = self.predict_action(state_dict, features=features)
        actor_confidence = float(action_result.get('confidence', max(action_result['probabilities'])))
        adjusted_actor_decision, actor_adjustment_reason = self._risk_adjust_actor_decision(
            state_dict,
            raw_predicted_cvar,
            action_result['decision'],
            actor_confidence,
        )
        calibrated = self._calibrate_prediction(
            state_dict,
            raw_predicted_cvar,
            adjusted_actor_decision,
            actor_confidence,
            action_result.get('warmup_factor', 1.0),
        )

        stabilized_cvar, final_decision, stabilized_confidence, stability_factor, hysteresis_applied = self._stabilize_output(
            calibrated['predicted_cvar_ms'],
            calibrated['final_decision'],
            calibrated['confidence'],
        )
        calibrated['predicted_cvar_ms'] = stabilized_cvar
        calibrated['confidence'] = stabilized_confidence

        final_power = action_result['power']
        if final_decision == 'ALLOWED' and final_power == 'INCREASE':
            final_power = 'REDUCE'
        elif final_decision == 'BLOCKED':
            final_power = 'INCREASE'

        policy_action, policy_power_percent, policy_profile = self._select_policy_action(
            final_decision,
            final_power,
            calibrated['predicted_cvar_ms'],
            _bounded_float(state_dict.get('cvar_ms', calibrated['predicted_cvar_ms']), 0, 500),
            _bounded_float(state_dict.get('cvar_trend', 0), -500, 500),
            calibrated['confidence'],
            calibrated['risk_score'],
        )
        self._store_prediction(
            calibrated['predicted_cvar_ms'],
            final_decision,
            calibrated['confidence'],
            policy_action,
        )
        
        result = {
            'predicted_cvar_ms': calibrated['predicted_cvar_ms'],
            'raw_predicted_cvar_ms': raw_predicted_cvar,
            'a3c_decision_raw': action_result['decision'],
            'a3c_decision': adjusted_actor_decision,
            'actor_adjustment_reason': actor_adjustment_reason,
            'decision_probabilities': action_result.get('decision_probabilities', []),
            'final_decision': final_decision,
            'power': final_power,
            'policy_action': policy_action,
            'policy_power_percent': policy_power_percent,
            'policy_profile': policy_profile,
            'confidence': calibrated['confidence'],
            'actor_confidence': actor_confidence,
            'actor_entropy': action_result.get('entropy', 0.0),
            'actor_margin': action_result.get('margin', 0.0),
            'critic_value': action_result.get('critic_value', 0.0),
            'warmup_factor': action_result.get('warmup_factor', 1.0),
            'stability_factor': stability_factor,
            'risk_score': calibrated['risk_score'],
            'risk_band': calibrated['risk_band'],
            'hysteresis_applied': hysteresis_applied,
            'calibrated': calibrated['calibrated'],
            'calibration_reason': calibrated['calibration_reason'],
            'state_sequence_len': len(self._state_history),
            'throughput_mbps_model': float(features[FEATURE_ORDER.index('throughput_mbps')]),
            'model_version': 'SBiLSTM + A3C V7 + GreenRAN calibration'
        }
        self._trace_prediction(state_dict, result)
        return result


if __name__ == "__main__":
    predictor = DRLPredictor()
    predictor.load_models()
    
    test_state = {
        'cvar_ms': 55,
        'cvar_trend': 2,
        'cvar_acceleration': 0.5,
        'latency_p95_ms': 65,
        'jitter_ms': 3,
        'packet_loss_pct': 0.2,
        'throughput_mbps': 120,
        'active_ues': 25,
        'active_cameras': 4,
        'critical_ues': 3,
        'camera_ratio': 0.16,
        'critical_ue_ratio': 0.12,
        'allocated_rbs': 250,
        'current_power': 22,
        'power_budget': 30,
        'hour_sin': 0,
        'hour_cos': 1,
        'variance_ms2': 12
    }
    
    result = predictor.predict(test_state)
    print("\n" + "="*50)
    print("DRL Prediction Result")
    print("="*50)
    for key, value in result.items():
        print(f"  {key}: {value}")
