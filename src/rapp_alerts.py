#!/usr/bin/env python3
"""
GreenRAN O-RAN - rApp Alert Manager
====================================

Responsabilidade: Sistema de alertas proativos
- Prediz SLA violations antes que ocorram
- Monitora xApps unresponsive
- Monitora timeouts de ACK
- Envia alertas por email

Uso:
    from rapp_alerts import AlertManager
    
    alerts = AlertManager()
    alerts.check_and_alert()
"""

import os
import sys
import json
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from greenran_paths import STATE_DIR, XAPP_HEALTH_PATH, RAPP_POLICIES_DIR, as_str

ALERT_LOG = as_str(STATE_DIR / "rapp_alerts.log")
EMAIL_CONFIG = as_str(STATE_DIR / "rapp_email_config.json")
XAPP_HEALTH_FILE = as_str(XAPP_HEALTH_PATH)
POLICY_STATUS_FILE = as_str(RAPP_POLICIES_DIR / "policy_status.json")


class AlertManager:
    """
    Gerenciador de alertas do rApp.
    
    Tipos de alertas:
    - SLA_VIOLATION_IMMINENT: Latência subindo rapidamente
    - XAPP_UNRESPONSIVE: xApp não responde há >60s
    - DECISION_ACK_TIMEOUT: Ack não recebido há >10s
    - LINK_QUALITY_DEGRADED: Qualidade do enlace ruim
    """
    
    def __init__(self, alert_log=ALERT_LOG, email_config=EMAIL_CONFIG):
        self.alert_log = alert_log
        self.email_config_file = email_config
        self.alerts_sent = []
        self.last_alert_time = {}
        self.alert_cooldown = 300  # 5 minutos entre alertas do mesmo tipo
        
        self.email_config = self._load_email_config()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Garante que arquivo de log existe."""
        if not os.path.exists(self.alert_log):
            with open(self.alert_log, 'w') as f:
                f.write(f"# GreenRAN rApp Alert Log\n")
                f.write(f"# Started: {datetime.now().isoformat()}\n")
    
    def _load_email_config(self):
        """Carrega configuração de email."""
        if os.path.exists(self.email_config_file):
            try:
                with open(self.email_config_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        
        return {
            'enabled': False,
            'smtp_server': 'localhost',
            'smtp_port': 587,
            'sender': 'rapp@greenran.local',
            'recipients': [],
            'use_tls': True
        }
    
    def _log_alert(self, alert_type, severity, message, details=None):
        """Registra alerta no log."""
        timestamp = datetime.now().isoformat()
        log_entry = f"[{timestamp}] [{severity}] [{alert_type}] {message}"
        
        if details:
            log_entry += f" | Details: {json.dumps(details)}"
        
        print(f"[ALERT] {log_entry}")
        
        try:
            with open(self.alert_log, 'a') as f:
                f.write(log_entry + "\n")
        except:
            pass
        
        return {
            'timestamp': timestamp,
            'type': alert_type,
            'severity': severity,
            'message': message,
            'details': details
        }
    
    def _should_send_alert(self, alert_type):
        """Verifica se deve enviar alerta (cooldown)."""
        now = time.time()
        last_time = self.last_alert_time.get(alert_type, 0)
        
        if now - last_time < self.alert_cooldown:
            return False
        
        self.last_alert_time[alert_type] = now
        return True
    
    def _send_email(self, subject, body, priority='normal'):
        """Envia email de alerta."""
        if not self.email_config.get('enabled', False):
            return False
        
        if not self.email_config.get('recipients'):
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[GreenRAN {priority.upper()}] {subject}"
            msg['From'] = self.email_config['sender']
            msg['To'] = ', '.join(self.email_config['recipients'])
            
            html_body = f"""
            <html>
            <body>
            <h2>GreenRAN rApp Alert</h2>
            <p><strong>Type:</strong> {subject}</p>
            <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <hr>
            <pre>{body}</pre>
            <hr>
            <p><small>Sent by rApp-ResourceOptimizer</small></p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            if self.email_config.get('use_tls', True):
                server = smtplib.SMTP(self.email_config['smtp_server'], self.email_config['smtp_port'])
                server.starttls()
            else:
                server = smtplib.SMTP(self.email_config['smtp_server'], self.email_config['smtp_port'])
            
            if self.email_config.get('username'):
                server.login(self.email_config['username'], self.email_config.get('password', ''))
            
            server.sendmail(
                self.email_config['sender'],
                self.email_config['recipients'],
                msg.as_string()
            )
            server.quit()
            
            print(f"[ALERT] Email enviado para {self.email_config['recipients']}")
            return True
        
        except Exception as e:
            print(f"[ALERT] ERRO ao enviar email: {e}")
            return False
    
    def check_xapp_health(self):
        """Verifica se xApps estão responsivos."""
        if not os.path.exists(XAPP_HEALTH_FILE):
            return []
        
        alerts = []
        
        try:
            with open(XAPP_HEALTH_FILE, 'r') as f:
                health = json.load(f)
            
            now = time.time()
            
            for xapp_name, data in health.items():
                status = data.get('status', 'UNKNOWN')
                
                if status == 'UNRESPONSIVE':
                    if self._should_send_alert(f'XAPP_UNRESPONSIVE_{xapp_name}'):
                        alert = self._log_alert(
                            'XAPP_UNRESPONSIVE',
                            'WARNING',
                            f'{xapp_name} não está responsivo',
                            {
                                'xapp': xapp_name,
                                'pid': data.get('pid'),
                                'last_heartbeat': data.get('last_heartbeat'),
                                'last_cycle': data.get('last_cycle')
                            }
                        )
                        alerts.append(alert)
                        
                        self._send_email(
                            f'xApp {xapp_name} Unresponsive',
                            f"xApp {xapp_name} não está respondendo.\n\n"
                            f"Status: {status}\n"
                            f"PID: {data.get('pid')}\n"
                            f"Último heartbeat: {data.get('last_heartbeat')}\n"
                            f"Último cycle: {data.get('last_cycle')}\n",
                            'warning'
                        )
                
                elif status == 'FAILED':
                    if self._should_send_alert(f'XAPP_FAILED_{xapp_name}'):
                        alert = self._log_alert(
                            'XAPP_FAILED',
                            'CRITICAL',
                            f'{xapp_name} falhou',
                            {'xapp': xapp_name, 'error': data.get('error')}
                        )
                        alerts.append(alert)
                        
                        self._send_email(
                            f'xApp {xapp_name} FAILED',
                            f"xApp {xapp_name} falhou completamente.\n\n"
                            f"Erro: {data.get('error')}\n"
                            f"Total restarts: {data.get('total_restarts', 0)}\n",
                            'critical'
                        )
        
        except Exception as e:
            print(f"[ALERT] ERRO ao verificar xApp health: {e}")
        
        return alerts
    
    def check_ack_timeout(self):
        """Verifica ACKs pendentes com timeout."""
        if not os.path.exists(POLICY_STATUS_FILE):
            return []
        
        alerts = []
        
        try:
            with open(POLICY_STATUS_FILE, 'r') as f:
                status = json.load(f)
            
            pending = status.get('pending', {})
            
            for ptype, data in pending.items():
                attempts = data.get('attempts', 0)
                
                if attempts >= 3:
                    if self._should_send_alert(f'ACK_TIMEOUT_{ptype}'):
                        alert = self._log_alert(
                            'DECISION_ACK_TIMEOUT',
                            'WARNING',
                            f'ACK timeout para política {ptype}',
                            {
                                'policy_type': ptype,
                                'attempts': attempts,
                                'last_sent': data.get('last_sent'),
                                'policy_id': data.get('policy_id')
                            }
                        )
                        alerts.append(alert)
                        
                        self._send_email(
                            f'ACK Timeout: {ptype}',
                            f"Política {ptype} não recebeu confirmação.\n\n"
                            f"Tentativas: {attempts}\n"
                            f"Último envio: {data.get('last_sent')}\n"
                            f"Policy ID: {data.get('policy_id')}\n",
                            'warning'
                        )
        
        except Exception as e:
            print(f"[ALERT] ERRO ao verificar ACK timeout: {e}")
        
        return alerts
    
    def check_sla_violation_trend(self, pattern_engine):
        """Verifica tendência de SLA violation."""
        alerts = []
        
        try:
            extended = pattern_engine.analyze_extended_metrics(15)
            
            if extended.get('status') != 'analyzed':
                return alerts
            
            trend = extended.get('trends', {}).get('latency', 'unknown')
            latency_status = extended.get('latency_status', 'unknown')
            quality_score = extended.get('quality_score', 100)
            
            # Se latência está piorando E já está em nível crítico
            if trend == 'increasing' and latency_status == 'poor':
                if self._should_send_alert('SLA_VIOLATION_IMMINENT'):
                    alert = self._log_alert(
                        'SLA_VIOLATION_IMMINENT',
                        'CRITICAL',
                        'SLA violation iminente - latência subindo rapidamente',
                        {
                            'trend': trend,
                            'latency_status': latency_status,
                            'quality_score': quality_score,
                            'avg_latency_us': extended.get('global_metrics', {}).get('avg_latency_us')
                        }
                    )
                    alerts.append(alert)
                    
                    self._send_email(
                        'SLA VIOLATION IMMINENT',
                        f"ALERTA CRÍTICO: Violação de SLA iminente!\n\n"
                        f"Tendência: {trend.upper()}\n"
                        f"Status de latência: {latency_status.upper()}\n"
                        f"Quality Score: {quality_score:.0f}/100\n"
                        f"Latência média: {extended.get('global_metrics', {}).get('avg_latency_us', 0)/1000:.1f}ms\n\n"
                        f"Recomendação: Priorizar recursos para câmeras.\n",
                        'critical'
                    )
        
        except Exception as e:
            print(f"[ALERT] ERRO ao verificar SLA violation: {e}")
        
        return alerts
    
    def check_link_quality(self, pattern_engine):
        """Verifica qualidade do enlace."""
        alerts = []
        
        try:
            link = pattern_engine.analyze_link_quality(15)
            
            if link.get('status') != 'analyzed':
                return alerts
            
            quality = link.get('quality', 'unknown')
            
            if quality == 'poor':
                if self._should_send_alert('LINK_QUALITY_DEGRADED'):
                    alert = self._log_alert(
                        'LINK_QUALITY_DEGRADED',
                        'WARNING',
                        'Qualidade do enlace degradada',
                        {
                            'quality': quality,
                            'mcs_avg': link.get('global_mcs_avg'),
                            'mcs_min': link.get('global_mcs_min'),
                            'mcs_score': link.get('mcs_score')
                        }
                    )
                    alerts.append(alert)
                    
                    self._send_email(
                        'Link Quality Degraded',
                        f"Qualidade do enlace de rádio está ruim.\n\n"
                        f"MCS médio: {link.get('global_mcs_avg', 0):.1f}\n"
                        f"MCS mínimo: {link.get('global_mcs_min', 0):.1f}\n"
                        f"MCS Score: {link.get('mcs_score', 0):.0f}/100\n\n"
                        f"Recomendação: {link.get('energy_reason', 'N/A')}\n",
                        'warning'
                    )
        
        except Exception as e:
            print(f"[ALERT] ERRO ao verificar link quality: {e}")
        
        return alerts
    
    def check_and_alert(self, pattern_engine=None):
        """
        Executa todas as verificações de alerta.
        
        Args:
            pattern_engine: Instância do PatternRecognition (opcional)
        
        Returns:
            Lista de alertas gerados.
        """
        all_alerts = []
        
        # Verifica xApp health
        all_alerts.extend(self.check_xapp_health())
        
        # Verifica ACK timeout
        all_alerts.extend(self.check_ack_timeout())
        
        # Verifica tendência SLA se pattern engine disponível
        if pattern_engine:
            all_alerts.extend(self.check_sla_violation_trend(pattern_engine))
            all_alerts.extend(self.check_link_quality(pattern_engine))
        
        return all_alerts
    
    def get_recent_alerts(self, count=10):
        """Retorna alertas recentes do log."""
        alerts = []
        
        if not os.path.exists(self.alert_log):
            return alerts
        
        try:
            with open(self.alert_log, 'r') as f:
                lines = f.readlines()
            
            for line in reversed(lines[-count:]):
                line = line.strip()
                if line and not line.startswith('#'):
                    alerts.append(line)
        
        except Exception as e:
            print(f"[ALERT] ERRO ao ler alertas: {e}")
        
        return alerts
    
    def create_email_config(self, smtp_server, smtp_port, sender, recipients,
                          username=None, password=None, use_tls=True):
        """Cria arquivo de configuração de email."""
        config = {
            'enabled': True,
            'smtp_server': smtp_server,
            'smtp_port': smtp_port,
            'sender': sender,
            'recipients': recipients if isinstance(recipients, list) else [recipients],
            'username': username,
            'password': password,
            'use_tls': use_tls
        }
        
        try:
            with open(self.email_config_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            self.email_config = config
            print(f"[ALERT] Configuração de email salva em {self.email_config_file}")
            return True
        
        except Exception as e:
            print(f"[ALERT] ERRO ao salvar configuração: {e}")
            return False


def main():
    """Teste do Alert Manager."""
    print("=" * 60)
    print("rApp Alert Manager - Teste")
    print("=" * 60)
    
    alerts = AlertManager()
    
    print("\n[1] Configuração de email:")
    print(f"    Enabled: {alerts.email_config.get('enabled', False)}")
    print(f"    SMTP: {alerts.email_config.get('smtp_server', 'N/A')}")
    print(f"    Recipients: {alerts.email_config.get('recipients', [])}")
    
    print("\n[2] Verificando xApp health...")
    xapp_alerts = alerts.check_xapp_health()
    print(f"    Alertas xApp: {len(xapp_alerts)}")
    
    print("\n[3] Verificando ACK timeout...")
    ack_alerts = alerts.check_ack_timeout()
    print(f"    Alertas ACK: {len(ack_alerts)}")
    
    print("\n[4] Alertas recentes:")
    recent = alerts.get_recent_alerts(5)
    for alert in recent:
        print(f"    {alert}")
    
    print("\n" + "=" * 60)
    print("Teste concluído!")
    print("=" * 60)


if __name__ == '__main__':
    main()
