/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
*   Copyright (c) 2011 Centre Tecnologic de Telecomunicacions de Catalunya (CTTC)
*   Copyright (c) 2015, NYU WIRELESS, Tandon School of Engineering, New York University
*   Copyright (c) 2016, 2018, University of Padova, Dep. of Information Engineering, SIGNET lab.
*   Copyright (c) 2024 Orange Innovation Egypt
*
*   This program is free software; you can redistribute it and/or modify
*   it under the terms of the GNU General Public License version 2 as
*   published by the Free Software Foundation;
*
*   This program is distributed in the hope that it will be useful,
*   but WITHOUT ANY WARRANTY; without even the implied warranty of
*   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
*   GNU General Public License for more details.
*
*   You should have received a copy of the GNU General Public License
*   along with this program; if not, write to the Free Software
*   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
*
*   Author: Marco Miozzo <marco.miozzo@cttc.es>
*           Nicola Baldo  <nbaldo@cttc.es>
*
*   Modified by: Marco Mezzavilla < mezzavilla@nyu.edu>
*                         Sourjya Dutta <sdutta@nyu.edu>
*                         Russell Ford <russell.ford@nyu.edu>
*                         Menglei Zhang <menglei@nyu.edu>
*                         Mostafa Ashraf <mostafa.ashraf.ext@orange.com>
*                         Aya Kamal <aya.kamal.ext@orange.com>
*                         Abdelrhman Soliman <abdelrhman.soliman.ext@orange.com>
*
*        Modified by: Tommaso Zugno <tommasozugno@gmail.com>
*								 Integration of Carrier Aggregation
*/



#ifndef SRC_MMWAVE_MODEL_MMWAVE_ENB_NET_DEVICE_H_
#define SRC_MMWAVE_MODEL_MMWAVE_ENB_NET_DEVICE_H_


#include "mmwave-net-device.h"
#include "ns3/event-id.h"
#include "ns3/traced-callback.h"
#include "ns3/nstime.h"
#include "mmwave-phy.h"
#include "mmwave-enb-phy.h"
#include "mmwave-enb-mac.h"
#include "mmwave-mac-scheduler.h"
#include <vector>
#include <map>
#include <deque>
#include <mutex>
#include <ns3/lte-enb-rrc.h>
#include <ns3/oran-interface.h>
#include "ns3/mmwave-bearer-stats-calculator.h"
#include <ns3/mmwave-phy-trace.h>



#include "TestCond-Type.h"
#include "TestCond-Expression.h"
#include "TestCond-Value.h"
#include "E2SM-KPM-ActionDefinition.h"
#include <functional>


namespace ns3 {
/* Add forward declarations here */
    class Packet;

    class PacketBurst;

    class Node;

    class LteEnbComponentCarrierManager;

    class MmWaveRadioEnergyModelEnb;

  namespace mmwave {
//class MmWavePhy;
        class MmWaveEnbPhy;

        class MmWaveEnbMac;

        typedef std::pair <uint64_t, uint16_t> ImsiCellIdPair_t;


        bool lessThan(int x, int y);
        bool greaterThan(int x, int y);
        bool equal(int x, int y);

      // Declare the MATH_CALL_BACKS vector
      extern std::vector<std::function<bool(int, int)>> MATH_CALL_BACKS;

      using ::ns3::MmWaveRadioEnergyModelEnb;

      class MmWaveEnbNetDevice : public MmWaveNetDevice {
        public:
            const static uint16_t E2SM_REPORT_MAX_NEIGH = 8;

            static TypeId GetTypeId(void);

            MmWaveEnbNetDevice();
            // MmWaveEnbNetDevice(Ptr<E2Termination> e2Termination);

            virtual ~MmWaveEnbNetDevice(void);

            virtual void DoDispose(void) override;

            virtual bool DoSend(Ptr<Packet> packet, const Address &dest, uint16_t protocolNumber) override;

            Ptr<MmWaveEnbPhy> GetPhy(void) const;

            Ptr<MmWaveEnbPhy> GetPhy(uint8_t index);

            uint16_t GetCellId() const;

            std::map<uint16_t, Ptr<UeManager>> GetUeMap ();

            bool HasCellId(uint16_t cellId) const;

            uint8_t GetBandwidth() const;

            void SetBandwidth(uint8_t bw);

            Ptr<MmWaveEnbMac> GetMac(void);

            Ptr<MmWaveEnbMac> GetMac(uint8_t index);

            void SetRrc(Ptr<LteEnbRrc> rrc);

            Ptr<LteEnbRrc> GetRrc(void);

            void SetE2Termination(Ptr<E2Termination> e2term);

            Ptr<E2Termination> GetE2Termination() const;

            void SetCcMap(std::map <uint8_t, Ptr<MmWaveComponentCarrier>> ccm) override;

            void BuildAndSendReportMessage(E2Termination::RicSubscriptionRequest_rval_s params = E2Termination::RicSubscriptionRequest_rval_s());

            void KpmSubscriptionCallback(E2AP_PDU_t *sub_req_pdu);

            bool ControlMessageReceivedCallback(E2AP_PDU_t *sub_req_pdu);
            // E2 callbacks run on the E2 termination thread.  Queue control
            // requests there and apply them from the ns-3 simulation thread.
            // This keeps the transport ACK responsive without treating it as
            // proof that the radio state was already changed.
            void ProcessPendingTasamControls();
            bool PrepareTasamSchedulerPolicy(uint64_t transactionId, uint64_t imsi,
                                             uint16_t minDlShareBp, uint16_t minUlShareBp,
                                             uint16_t surplusWeightBp);
            bool CommitTasamSchedulerPolicy(uint64_t transactionId, uint16_t expectedUes,
                                            uint32_t ttlMs,
                                            uint16_t maxDiscretionaryDlSymbolsBp = 10000);
            void ClearTasamSchedulerPolicy(void);
            void ClearTasamControl(void);
            bool SetTasamTxPowerPercent(uint16_t targetCellId, uint16_t powerPercent,
                                         uint64_t transactionId = 0, uint32_t ttlMs = 0);
            // O helper de energia registra o modelo causal diretamente no
            // device: GetObject pode falhar quando a agregação não está
            // visível no caminho de controle, e sem o ponteiro os cortes de
            // potência não afetam a energia nativa (evidência r6i).
            void SetRadioEnergyModel(Ptr<MmWaveRadioEnergyModelEnb> model);
            Ptr<MmWaveRadioEnergyModelEnb> GetRadioEnergyModel(void) const;
            uint64_t GetActiveTasamTransaction(void) const;
            uint64_t GetActiveTasamPowerTransaction(void) const;
            // The applied power transaction remains observable after its
            // lease expires. Lease freshness is reported separately so
            // persistent power state cannot be confused with a renewable
            // control lease.
            uint64_t GetTasamPowerTransaction(void) const;
            bool IsTasamPowerLeaseFresh(void) const;
            uint16_t GetActiveTasamUeCount(void) const;
            uint64_t GetActiveTasamAllocatedDlSymbols(void) const;
            uint64_t GetActiveTasamDlSymbolCapacity(void) const;
            uint16_t GetActiveTasamDiscretionaryDlSymbolsBp(void) const;
            uint64_t GetActiveTasamMandatoryDlSymbols(void) const;
            uint64_t GetActiveTasamDiscretionaryDlSymbols(void) const;
            uint64_t GetActiveTasamWithheldDlSymbols(void) const;
            uint16_t GetTasamTxPowerPercent(void) const;
            double GetTasamNominalTxPowerDbm(void) const;
            double GetTasamPowerExpirySimTime(void) const;
            std::string GetAssociationEpoch(void) const;
            std::string GetCampaignId(void) const;
            void SetStartTime(uint64_t);

            void stopSendingAndCancelSchedule();

        protected:
            virtual void DoInitialize(void) override;

            void UpdateConfig();

            void GetPeriodicPdcpStats();


        private:

            bool m_stopSendingMessages;

            Ptr<MmWaveMacScheduler> m_scheduler;

            Ptr<LteEnbRrc> m_rrc;

            uint16_t m_cellId;       /* Cell Identifer. To uniquely identify an E-nodeB  */

            uint8_t m_Bandwidth;       /* bandwidth in RBs (?) */

            Ptr<LteEnbComponentCarrierManager> m_componentCarrierManager; ///< the component carrier manager of this eNb

            bool m_isConfigured;

            Ptr<E2Termination> m_e2term;
            Ptr<MmWaveBearerStatsCalculator> m_e2PdcpStatsCalculator;
            Ptr<MmWaveBearerStatsCalculator> m_e2RlcStatsCalculator;
            Ptr<MmWavePhyTrace> m_e2DuCalculator;

            bool m_is_reported = false;
            int DL_PRBvalue ; 
            double m_e2Periodicity;

            // TODO doxy
            Ptr<KpmIndicationHeader> BuildRicIndicationHeader(std::string plmId, std::string gnbId, uint16_t nrCellId);

            Ptr<KpmIndicationMessage> BuildRicIndicationMessageCuUp(std::string plmId);

            Ptr<KpmIndicationMessage> BuildRicIndicationMessageCuCp(std::string plmId);

            Ptr<KpmIndicationMessage> BuildRicIndicationMessageDu(std::string plmId, uint16_t nrCellId);

            //traces for gui
            Ptr<KpmIndicationMessage> BuildGUIDu(std::string plmId, uint16_t nrCellId);
            Ptr<KpmIndicationMessage> BuildGUICuCp(std::string plmId);
            Ptr<KpmIndicationMessage> BuildGUICuUp(std::string plmId);

            std::string GetImsiString(uint64_t imsi);

            uint32_t GetRlcBufferOccupancy(Ptr<LteRlc> rlc) const;

            bool m_sendCuUp;
            bool m_sendCuCp;
            bool m_sendDu;

            static void
            RegisterNewSinrReadingCallback(Ptr<MmWaveEnbNetDevice> netDev, std::string context, uint64_t imsi,
                                           uint16_t cellId, long double sinr);

            void RegisterNewSinrReading(uint64_t imsi, uint16_t cellId, long double sinr);

            std::map <uint64_t, std::map<uint16_t, long double>> m_l3sinrMap;
            uint64_t m_startTime;
            std::map <uint64_t, uint32_t> m_drbThrDlPdcpBasedComputationUeid;
            std::map <uint64_t, uint32_t> m_drbThrDlUeid;
            bool m_isReportingEnabled; //! true is KPM reporting cycle is active, false otherwise
            bool m_reducedPmValues; //< if true use a reduced subset of pmvalues

            uint16_t m_basicCellId;
            double  rc_e2_func_id ; // to RC
            double e2_func_id; //to pass kpm function id
            bool m_e2andlog; //if true, both e2 term and e2file logging will work
            bool m_forceE2FileLogging; //< if true log PMs to files
            std::string m_cuUpFileName;
            std::string m_cuCpFileName;
            std::string m_duFileName;

            double CalculatePrbAverage (void);
            void CheckReportingFlag (void);

            void NewFunction (bool m_is_reported);

            std::vector<double> m_prbHistory;
            static const size_t MAX_PRB_HISTORY = 10;
            Time m_checkPeriod;
            E2Termination::RicSubscriptionRequest_rval_s m_lastSubscriptionParams;
            bool m_hasValidSubscription;
            double m_tasamNominalTxPowerDbm{-1.0};
            uint16_t m_tasamTxPowerPercent{100};
            uint64_t m_tasamPowerTransaction{0};
            Time m_tasamPowerExpiry{Seconds (0)};
            Ptr<MmWaveRadioEnergyModelEnb> m_radioEnergyModel;

            struct PendingTasamControl
            {
              enum Kind
              {
                PREPARE,
                COMMIT,
                CLEAR,
                POWER
              };

              Kind kind{CLEAR};
              uint64_t transactionId{0};
              uint64_t imsi{0};
              uint16_t minDlShareBp{0};
              uint16_t minUlShareBp{0};
              uint16_t surplusWeightBp{0};
              uint16_t expectedUes{0};
              uint16_t maxDiscretionaryDlSymbolsBp{10000};
              uint16_t targetCellId{0};
              uint16_t powerPercent{100};
              uint32_t ttlMs{0};
            };

            mutable std::mutex m_tasamControlMutex;
            std::deque<PendingTasamControl> m_pendingTasamControls;
       
   


      };
  } 
 }



#endif /* SRC_MMWAVE_MODEL_MMWAVE_ENB_NET_DEVICE_H_ */
