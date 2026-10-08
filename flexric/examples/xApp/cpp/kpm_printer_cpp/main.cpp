#include <atomic>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <thread>
#include <chrono>

extern "C" {
#include "../../../../src/xApp/e42_xapp_api.h"
#include "../../../../src/util/conf_file.h"
}

static std::atomic<bool> g_run{true};

static void on_sig(int) {
  g_run = false;
  // tenta pedir stop limpo (não é obrigatório, mas é bom)
  try_stop_xapp_api();
}

// ======= 1) CALLBACK: chega aqui quando vier INDICATION =======
static void kpm_cb(sm_ag_if_rd_t const* rd)
{
  if (!rd) return;

  // xapp_kpm_moni.c faz asserts de tipo e versão.
  // Aqui vamos ser mais "robustos": só imprimimos o tipo.
  std::cout << "[kpm_printer_cpp] indication recebida. rd->type=" << (int)rd->type << "\n";

  // Se você quiser imprimir métricas específicas KPM:
  // Use o mesmo parsing do xapp_kpm_moni.c dentro daqui (função sm_cb_kpm).
  // A parte de parsing depende da versão do KPM (no seu CMake: KPM_V2_03 / KPM_V2_03).
}

// ======= 2) BUILDER DA SUBSCRIPTION (data) =======
// O e42_xapp_api.h diz: report_sm_xapp_api(..., void* data, ...)
// Esse "data" é o request de REPORT do SM (KPM).
//
// A forma REALISTA é construir igual ao examples/xApp/c/monitor/xapp_kpm_moni.c.
//
// Então: você vai copiar do xapp_kpm_moni.c a função que cria o "data" (o report).
// Vou deixar aqui um protótipo para ficar explícito:
static void* build_kpm_report_data(uint64_t period_ms)
{
  (void)period_ms;

  // TODO (muito importante):
  // Copie do arquivo:
  //   ~/orange_nuclear/flexric/examples/xApp/c/monitor/xapp_kpm_moni.c
  // a parte que monta o "data" passado para report_sm_xapp_api().
  //
  // Normalmente é algo como:
  //   kpm_report_data_t* data = calloc(1, sizeof(...));
  //   data->... = ...
  //   return data;
  //
  // DICA: procure no xapp_kpm_moni.c por "report_sm_xapp_api(" e veja o que ele passa como 3º argumento.
  return nullptr;
}

int main(int argc, char** argv)
{
  std::signal(SIGINT, on_sig);
  std::signal(SIGTERM, on_sig);

  uint64_t period_ms = 1000;
  if (argc >= 2) {
    period_ms = (uint64_t)std::strtoull(argv[1], nullptr, 10);
  }

  // Lê flexric.conf do jeito padrão do projeto (fr_args_t)
  fr_args_t args = init_fr_args(argc, argv);

  std::cout << "[kpm_printer_cpp] init_xapp_api...\n";
  init_xapp_api(&args);

  // Descobre nós E2 conectados
  e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
  std::cout << "[kpm_printer_cpp] E2 nodes conectados: " << nodes.len << "\n";

  if (nodes.len == 0) {
    std::cout << "[kpm_printer_cpp] nenhum node conectado. Deixe o RIC e o emu_agent_gnb rodando.\n";
  }

  // Monta payload de report KPM
  void* data = build_kpm_report_data(period_ms);
  if (!data) {
    std::cerr << "[kpm_printer_cpp] ERRO: build_kpm_report_data() retornou nullptr.\n"
              << "Copie do xapp_kpm_moni.c a construção do payload do report_sm_xapp_api().\n";
    return 1;
  }

  // Faz subscription/report para cada node
  // rf_id: RAN function ID do KPM. No FlexRIC, KPM costuma ser rf_id = 2 (mas pode variar).
  // Melhor: imprimir os ran functions do node e pegar o ID correto.
  // Como atalho, vamos começar com 2 e ajustar se precisar.
  const uint32_t KPM_RF_ID_GUESS = 2;

  for (size_t i = 0; i < nodes.len; ++i) {
    global_e2_node_id_t* id = &nodes.n[i].id;

    sm_ans_xapp_t ans = report_sm_xapp_api(id, KPM_RF_ID_GUESS, data, kpm_cb);
    if (!ans.success) {
      std::cerr << "[kpm_printer_cpp] report_sm_xapp_api falhou no node " << i
                << " motivo=" << (ans.u.reason ? ans.u.reason : (char*)"") << "\n";
    } else {
      std::cout << "[kpm_printer_cpp] subscription OK. handle=" << ans.u.handle << "\n";
    }
  }

  std::cout << "[kpm_printer_cpp] rodando. Ctrl+C para sair.\n";
  while (g_run) {
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
  }

  std::cout << "[kpm_printer_cpp] aguardando finalização da API...\n";
  xapp_wait_end_api();
  std::cout << "[kpm_printer_cpp] fim.\n";
  return 0;
}
