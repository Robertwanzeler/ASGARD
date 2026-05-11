#!/usr/bin/env python3
"""
GreenRAN - Priority Queue Implementation
========================================
Priority-based scheduling for cameras and sensors
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from queue import Queue, Empty
from datetime import datetime

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../../config/priority_classes.json")

@dataclass
class PriorityPacket:
    """Packet with priority information"""
    ue_id: int
    device_type: str  # camera, sensor, ue
    packet_size_bytes: int
    timestamp: float
    latency_us: float
    throughput_kbps: float
    priority: int = 0  # 1=CRITICAL, 4=LOW
    
@dataclass 
class PriorityClass:
    """Priority class configuration"""
    id: str
    name: str
    priority: int
    sla_latency_ms: Optional[float]
    sla_throughput_mbps: Optional[float]
    guaranteed_prb_percent: int
    max_queue_size: Optional[int]
    preemption: bool
    types: List[str]

class PriorityQueue:
    """Priority queue for network scheduling"""
    
    def __init__(self, config_path: str = CONFIG_PATH):
        self.load_config(config_path)
        self.queues = {}
        self.stats = {
            'total_packets': 0,
            'critical_packets': 0,
            'high_packets': 0,
            'normal_packets': 0,
            'low_packets': 0,
            'sla_violations': 0
        }
        self._init_queues()
    
    def load_config(self, config_path: str):
        """Load priority classes configuration"""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                self.classes = {
                    c['id']: PriorityClass(
                        id=c['id'],
                        name=c['name'],
                        priority=c['priority'],
                        sla_latency_ms=c.get('sla_latency_ms'),
                        sla_throughput_mbps=c.get('sla_throughput_mbps'),
                        guaranteed_prb_percent=c['guaranteed_prb_percent'],
                        max_queue_size=c.get('max_queue_size'),
                        preemption=c['preemption'],
                        types=c['types']
                    )
                    for c in config['classes']
                }
        except FileNotFoundError:
            self.classes = {}
    
    def _init_queues(self):
        """Initialize queues for each priority class"""
        for class_id, class_config in self.classes.items():
            max_size = class_config.max_queue_size or 1000
            self.queues[class_id] = Queue(maxsize=max_size)
    
    def _get_priority(self, device_type: str) -> str:
        """Determine priority class for device type"""
        for class_id, class_config in self.classes.items():
            if device_type in class_config.types:
                return class_id
        return "NORMAL"
    
    def enqueue(self, packet: PriorityPacket) -> bool:
        """Add packet to appropriate queue"""
        priority_class = self._get_priority(packet.device_type)
        packet.priority = self.classes[priority_class].priority
        
        try:
            self.queues[priority_class].put_nowait(packet)
            self.stats['total_packets'] += 1
            
            if priority_class == "CRITICAL":
                self.stats['critical_packets'] += 1
            elif priority_class == "HIGH":
                self.stats['high_packets'] += 1
            elif priority_class == "NORMAL":
                self.stats['normal_packets'] += 1
            else:
                self.stats['low_packets'] += 1
            
            return True
        except:
            return False
    
    def dequeue(self) -> Optional[PriorityPacket]:
        """Get next packet based on priority (CRITICAL first)"""
        # Try CRITICAL first, then HIGH, NORMAL, LOW
        for class_id in ["CRITICAL", "HIGH", "NORMAL", "LOW"]:
            try:
                packet = self.queues[class_id].get_nowait()
                return packet
            except Empty:
                continue
        return None
    
    def check_sla_violation(self, packet: PriorityPacket) -> bool:
        """Check if packet violates SLA"""
        priority_class = self._get_priority(packet.device_type)
        config = self.classes.get(priority_class)
        
        if not config:
            return False
        
        # Check latency SLA for cameras
        if config.sla_latency_ms and packet.latency_us / 1000 > config.sla_latency_ms:
            self.stats['sla_violations'] += 1
            return True
        
        # Check throughput SLA for cameras
        if config.sla_throughput_mbps and packet.throughput_kbps / 1000 < config.sla_throughput_mbps:
            self.stats['sla_violations'] += 1
            return True
        
        return False
    
    def get_queue_sizes(self) -> Dict[str, int]:
        """Get current size of all queues"""
        return {
            class_id: q.qsize() 
            for class_id, q in self.queues.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        return self.stats.copy()
    
    def get_sla_status(self) -> Dict[str, Any]:
        """Get SLA status for all priority classes"""
        status = {}
        for class_id, config in self.classes.items():
            queue_size = self.queues[class_id].qsize()
            utilization = queue_size / (config.max_queue_size or 1000) * 100
            
            status[class_id] = {
                'name': config.name,
                'queue_size': queue_size,
                'utilization_percent': utilization,
                'guaranteed_prb_percent': config.guaranteed_prb_percent,
                'sla_latency_ms': config.sla_latency_ms,
                'sla_throughput_mbps': config.sla_throughput_mbps
            }
        return status


class DecisionEngine:
    """Decision engine based on priority and SLA"""
    
    def __init__(self, priority_queue: PriorityQueue):
        self.priority_queue = priority_queue
    
    def make_decision(self, metrics: Dict) -> Dict[str, Any]:
        """Make scheduling decision based on metrics and priorities"""
        decision = {
            'action': 'MAINTAIN',
            'power_level': 100,
            'priority_class': 'NORMAL',
            'reason': 'No changes needed'
        }
        
        # Check camera SLAs first (CRITICAL priority)
        cameras = metrics.get('cameras', [])
        for camera in cameras:
            latency_ms = camera.get('latency_us', 0) / 1000
            throughput_mbps = camera.get('throughput_kbps', 0) / 1000
            
            # Throughput < 25 Mbps = BLOCKED
            if throughput_mbps < 25:
                decision['action'] = 'BLOCKED'
                decision['power_level'] = 100
                decision['priority_class'] = 'CRITICAL'
                decision['reason'] = f'Camera {camera.get("ue_id")} throughput {throughput_mbps:.1f}Mbps < 25Mbps (SLA violation)'
                return decision
            
            # Latency >= 80ms = BLOCKED
            if latency_ms >= 80:
                decision['action'] = 'BLOCKED'
                decision['power_level'] = 100
                decision['priority_class'] = 'CRITICAL'
                decision['reason'] = f'Camera {camera.get("ue_id")} latency {latency_ms:.1f}ms >= 80ms'
                return decision
            
            # Latency 60-80ms = CONDITIONAL
            if latency_ms >= 60:
                decision['action'] = 'CONDITIONAL'
                decision['power_level'] = 80
                decision['priority_class'] = 'CRITICAL'
                decision['reason'] = f'Camera {camera.get("ue_id")} latency {latency_ms:.1f}ms in [60-80ms] range'
        
        # Check sensor packet loss
        sensors = metrics.get('sensors', [])
        for sensor in sensors:
            packet_loss = sensor.get('packet_loss_percent', 0)
            
            if packet_loss >= 10:
                decision['action'] = 'BLOCKED'
                decision['power_level'] = 100
                decision['priority_class'] = 'HIGH'
                decision['reason'] = f'Sensor {sensor.get("ue_id")} packet loss {packet_loss:.1f}% >= 10%'
                return decision
            
            if packet_loss >= 5:
                if decision['action'] != 'BLOCKED':
                    decision['action'] = 'CONDITIONAL'
                    decision['power_level'] = max(decision['power_level'], 80)
        
        # If all cameras OK, allow energy saving
        if decision['action'] == 'MAINTAIN':
            decision['action'] = 'ALLOWED'
            decision['power_level'] = 60
            decision['reason'] = 'All SLAs OK - economy allowed'
        
        return decision


if __name__ == "__main__":
    # Test
    pq = PriorityQueue()
    print("Priority Queue initialized")
    print(f"Classes: {list(pq.classes.keys())}")
    
    # Test decision engine
    test_metrics = {
        'cameras': [
            {'ue_id': 1, 'latency_us': 1000, 'throughput_kbps': 26000},
            {'ue_id': 2, 'latency_us': 1500, 'throughput_kbps': 26000},
        ],
        'sensors': [
            {'ue_id': 4, 'packet_loss_percent': 2}
        ]
    }
    
    engine = DecisionEngine(pq)
    decision = engine.make_decision(test_metrics)
    print(f"\nDecision: {decision}")