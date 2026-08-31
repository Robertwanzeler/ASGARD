# Medição energética calibrada do GreenRAN

O GreenRAN agora calcula uma estimativa energética para os componentes
controlados pelo xApp Energy Saver: uma RU e um enlace mmWave.

## Como é calculada

Cada comando de energia registra o estado e um timestamp de alta resolução.
Entre dois comandos, o estado anterior é considerado constante:

```text
energia_J = potência_W × duração_s
```

A potência usa o modelo:

```text
potência = unidades × (potência_ociosa + potência_dinâmica × nível_percentual)
```

Assim, reduzir para 25% reduz a parcela dinâmica, mas não faz uma RU ativa
consumir zero watts.

## Calibração

Os valores ficam em [`config/energy_calibration.json`](../config/energy_calibration.json).
Os valores atuais são provisórios:

- RU: 80 W ociosa, 250 W ativa;
- mmWave: 40 W ociosa, 180 W ativa.

Eles devem ser substituídos pelos watts nominais do laboratório ou do
fabricante antes de uma conclusão física/publicação.

## Interpretação

Na comparação pareada:

```text
economia = (energia_baseline - energia_assistente) / energia_baseline
```

- valor positivo: o TA-SAM economizou;
- zero: consumo equivalente;
- valor negativo: o TA-SAM consumiu mais.

O resultado é uma estimativa calibrada, não uma leitura direta de wattímetro.
DU, transporte e UEs ficam fora da primeira versão. A dashboard informa J e W
modelados e deixa explícita essa limitação.
