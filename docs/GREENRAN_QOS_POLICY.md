# Política QoS operacional do GreenRAN

Esta política é a referência do cenário GreenRAN com câmeras 4K/IA e veículos
autônomos. Ela separa prioridade por segurança de prioridade por largura de
banda:

| Serviço | Prioridade | Garantia operacional |
|---|---:|---|
| Veículo autônomo em segurança | 1A | latência alvo de 20 ms e perda máxima de 1% |
| Câmera 4K/IA | 1B | 25 Mbps por câmera e latência menor que 100 ms |
| Sensor crítico | 2 | latência preferencial de até 100 ms e perda máxima de 5% |
| Usuário comum | 3 | melhor esforço |
| Background | 4 | recurso residual |

Veículos e câmeras são críticos, mas por motivos diferentes: o veículo precisa
de resposta imediata; a câmera precisa de vazão sustentada para a inferência de
IA. Em conflito, a latência de segurança veicular é atendida primeiro, sem
remover a reserva mínima necessária das câmeras.

O TA-SAM usa esta política no fine-tuning a partir do checkpoint da seed 45.
O ARMD continua como camada de interpretação e proteção. A fonte externa segue
somente como último recurso.
