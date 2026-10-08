/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/* *
 * Copyright (c) 2024 Orange Innovation Poland
 * Copyright (c) 2024 Orange Innovation Egypt
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 * Authors: Andrea Lacava <thecave003@gmail.com>
 *          Michele Polese <michele.polese@gmail.com>
 *          Argha Sen <arghasen10@gmail.com>
 *          Kamil Kociszewski <kamil.kociszewski@orange.com>
 *          Mostafa Ashraf <mostafa.ashraf.ext@orange.com>
 */

// Native E2 control evidence is part of the actuation smoke contract.
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/mobility-module.h"
#include "ns3/applications-module.h"
#include "ns3/point-to-point-helper.h"
#include <ns3/lte-ue-net-device.h>
#include "ns3/mmwave-helper.h"
#include "ns3/epc-helper.h"
#include "ns3/mmwave-point-to-point-epc-helper.h"
#include "../src/mmwave/model/node-container-manager.h"
#include "ns3/lte-helper.h"
#include <sys/time.h>
#include <ctime>
#include <sys/types.h>
#include <iostream>
#include <stdlib.h>
#include <list>
#include <random>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <vector>
#include <string>
#include "ns3/basic-energy-source-helper.h"
#include "ns3/mmwave-radio-energy-model-enb-helper.h"
#include "ns3/mmwave-flex-tti-mac-scheduler.h"
#include "ns3/isotropic-antenna-model.h"

using namespace ns3;
using namespace mmwave;

std::map<uint64_t, uint16_t> imsi_cellid;
std::map<uint16_t, std::set<uint64_t>> imsi_list;
std::map<uint16_t, Ptr < Node>>
    cellid_node;
std::map<uint32_t, uint16_t> ue_cellid_usinghandover;
std::map<uint64_t, uint32_t> ueimsi_nodeid;
int ue_assoc_list[64] = {0};
double maxXAxis;
double maxYAxis;
bool esON_list[10] = {0};
double totalnewEnergyConsumption_storage[10] = {0};
double totaloldEnergyConsumption_storage[10] = {0};
double current_energy_consumption[10] = {0};
double curr_total_energy_consumption = 0;
double max_energy_consumption = 0;
double sum_curr_total_energy_consumption = 0;
int num_of_mmdev = 0;
// v9-fidelity keeps native evidence compact.  The flag is process-local and
// is set from the command line before the simulator starts.
bool g_nativeAggregatedEvidenceEnabled = false;
std::map<uint16_t, std::string> g_lastAssociationSignature;
// Monotonic per-cell association epoch: incremented every time a cell's
// UE signature changes (handover in/out).  Exposed in the association
// trace so downstream evidence consumers can group rows by epoch.
std::map<uint16_t, uint64_t> g_associationEpoch;

static std::string
NativeEvidenceVersion ()
{
  const char* configured = std::getenv ("GREENRAN_NATIVE_EVIDENCE_VERSION");
  if (configured != nullptr && configured[0] != '\0')
    {
      return std::string (configured);
    }
  return g_nativeAggregatedEvidenceEnabled ? "v5" : "v3";
}

static std::string
NativeSourceGeneration ()
{
  const char* configured = std::getenv ("GREENRAN_NATIVE_SOURCE_GENERATION");
  if (configured != nullptr && configured[0] != '\0')
    {
      return std::string (configured);
    }
  return g_nativeAggregatedEvidenceEnabled ? "native-v5" : "native-v3";
}

// The Python orchestrator publishes this sidecar before sending an E2
// bundle.  ns-3 consumes it only to label native evidence; it is never used
// as proof of application.  The PHY readback and active scheduler snapshot
// remain the confirmation boundary in the Data Lake.
struct NativeControlContext
{
  uint64_t sequence = 0;
  uint64_t decisionId = 0;
  std::string correlation;
  std::string campaign;
  std::string generation;
  double simTime = 0.0;
  std::string sleepTransactionId;
};

static bool
ReadNativeControlContext (uint64_t sequence, NativeControlContext& context)
{
  if (sequence == 0)
    {
      return false;
    }
  const char* configured = std::getenv ("GREENRAN_NS3_NATIVE_CONTROL_CONTEXT_PATH");
  if (configured == nullptr || configured[0] == '\0')
    {
      return false;
    }
  std::ifstream input (configured);
  if (!input.is_open ())
    {
      return false;
    }
  std::string line;
  std::getline (input, line); // header
  bool found = false;
  while (std::getline (input, line))
    {
      std::vector<std::string> fields;
      std::stringstream stream (line);
      std::string field;
      while (std::getline (stream, field, ','))
        {
          fields.push_back (field);
        }
      if (fields.size () < 5)
        {
          continue;
        }
      try
        {
          const uint64_t rowSequence = std::stoull (fields[0]);
          if (rowSequence != sequence)
            {
              continue;
            }
          context.sequence = rowSequence;
          context.decisionId = std::stoull (fields[1]);
          context.correlation = fields[2];
          context.campaign = fields[3];
          context.generation = fields[4];
          if (fields.size () > 5 && !fields[5].empty ())
            {
              context.simTime = std::stod (fields[5]);
            }
          if (fields.size () > 6)
            {
              context.sleepTransactionId = fields[6];
            }
          found = true;
        }
      catch (const std::exception&)
        {
          continue;
        }
    }
  return found;
}

/**
 * Scenario Zero
 *
 */

NS_LOG_COMPONENT_DEFINE ("ScenarioZero");

void
PrintGnuplottableUeListToFile(std::string filename) {
    std::ofstream outFile;
    outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::trunc);
    if (!outFile.is_open()) {
        NS_LOG_ERROR("Can't open file " << filename);
        return;
    }
    for (NodeList::Iterator it = NodeList::Begin(); it != NodeList::End(); ++it) {
        Ptr <Node> node = *it;
        int nDevs = node->GetNDevices();
        for (int j = 0; j < nDevs; j++) {
            Ptr <LteUeNetDevice> uedev = node->GetDevice(j)->GetObject<LteUeNetDevice>();
            Ptr <MmWaveUeNetDevice> mmuedev = node->GetDevice(j)->GetObject<MmWaveUeNetDevice>();
            Ptr <McUeNetDevice> mcuedev = node->GetDevice(j)->GetObject<McUeNetDevice>();
            if (uedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << uedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            } else if (mmuedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << mmuedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            } else if (mcuedev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                outFile << "set label \"" << mcuedev->GetImsi() << "\" at " << pos.x << "," << pos.y
                        << " left font \"Helvetica,8\" textcolor rgb \"black\" front point pt 1 ps "
                           "0.3 lc rgb \"black\" offset 0,0"
                        << std::endl;
            }
        }
    }
}

void
PrintGnuplottableEnbListToFile(uint64_t m_startTime) {

  //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
  uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();
  //
  std::string filename1 = "enbs.txt";
  std::string filename2 = "gnbs.txt";
  //
  int mmnode_iterator = 0;
  curr_total_energy_consumption = 0;
  for (NodeList::Iterator it = NodeList::Begin(); it != NodeList::End(); ++it) {
      Ptr <Node> node = *it;
      int nDevs = node->GetNDevices();
      for (int j = 0; j < nDevs; j++) {
          Ptr <LteEnbNetDevice> enbdev = node->GetDevice(j)->GetObject<LteEnbNetDevice>();
          Ptr <MmWaveEnbNetDevice> mmdev = node->GetDevice(j)->GetObject<MmWaveEnbNetDevice>();
          if (enbdev) {
              Vector pos = node->GetObject<MobilityModel>()->GetPosition();
              std::ofstream outFile1;
              outFile1.open(filename1.c_str(), std::ios_base::out | std::ios_base::app);
              if (!outFile1.is_open()) {
                  NS_LOG_ERROR("Can't open file " << filename1);
                  return;
                }
                //outFile1 << timestamp << "," << enbdev->GetCellId() << "," << pos.x << "," << pos.y << pos.z << std::endl;
                outFile1 << timestamp << "," << enbdev->GetCellId() << "," << pos.x << "," << pos.y << ","
                         << m_startTime << "," << "0" << "," << "30" << std::endl;
                outFile1.close();
            } else if (mmdev) {
                Vector pos = node->GetObject<MobilityModel>()->GetPosition();
                std::ofstream outFile2;
                outFile2.open(filename2.c_str(), std::ios_base::out | std::ios_base::app);
                if (!outFile2.is_open()) {
                    NS_LOG_ERROR("Can't open file " << filename2);
                    return;
                }
                auto ueMap = mmdev->GetUeMap();
                Ptr<MmWaveEnbPhy> enbPhy = node->GetDevice(j)->GetObject<MmWaveEnbNetDevice>()->GetPhy();
                for (const auto &ue: ueMap) {
                    uint64_t imsi_assoc = ue.second->GetImsi();
                    //NS_LOG_UNCOND ("IMSI: " << imsi_assoc << " associated with cell: "  << mmdev->GetCellId ());
                    ue_assoc_list[imsi_assoc - 1] = mmdev->GetCellId();
                }
              uint16_t cell_id = mmdev->GetCellId();
              double es_power = enbPhy->GetTxPower();
              if (es_power == 0) {
                  esON_list[cell_id] = true;
                } else {
                    esON_list[cell_id] = false;
                }
              curr_total_energy_consumption =
                  curr_total_energy_consumption + current_energy_consumption[cell_id];
              //outFile2 << timestamp << "," << enbdev->GetCellId() << "," << pos.x << "," << pos.y << pos.z << std::endl;
              outFile2 << timestamp << "," << cell_id << "," << pos.x << "," << pos.y << ","
                       << m_startTime << "," << esON_list[cell_id] << ","
                       << current_energy_consumption[cell_id] << "," << max_energy_consumption
                       << "," << sum_curr_total_energy_consumption << std::endl;
              outFile2.close ();
            }
        }
    }
  if (mmnode_iterator == num_of_mmdev)
    {
      sum_curr_total_energy_consumption = curr_total_energy_consumption;
    }
  if (mmnode_iterator == num_of_mmdev && max_energy_consumption < curr_total_energy_consumption)
    {
      max_energy_consumption = curr_total_energy_consumption;
    }
}

void
ClearFile(std::string Filename, uint64_t m_startTime) {
    std::string filename = Filename;
    std::ofstream outFile;
    outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::trunc);
    if (!outFile.is_open()) {
        NS_LOG_ERROR("Can't open file " << filename);
        return;
    }
    outFile.close();
    //  struct timeval time_now{};
    //  gettimeofday (&time_now, nullptr);
    //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
    uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();
    std::ofstream outFile1;
    outFile1.open(filename.c_str(), std::ios_base::out | std::ios_base::app);

  if (Filename == "ue_position.txt") {
      outFile1 << "timestamp,id,x,y,type,cell,simid" << std::endl;
    }
  else
    {
      outFile1 << "timestamp,id,x,y,simid,ESstate,currEC,maxEC,totalcurrEC" << std::endl;
      outFile1 << timestamp << "," << "0" << "," << maxXAxis << "," << maxYAxis << std::endl;
    }
    outFile1.close();
}

void
PrintPosition(Ptr<Node> node, int iterator, std::string Filename, uint64_t m_startTime) {

    //uint64_t m_startTime = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
    uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now().GetMilliSeconds();

  int imsi;
  Ptr <Node> node1 = NodeList::GetNode(iterator);
  int nDevs = node->GetNDevices();
  std::string filename = Filename;
  std::ofstream outFile;
  for (int j = 0; j < nDevs; j++) {
      Ptr <McUeNetDevice> mcuedev = node1->GetDevice(j)->GetObject<McUeNetDevice>();
      Ptr <LteUeNetDevice> uedev = node->GetDevice(j)->GetObject<LteUeNetDevice>();
      Ptr <MmWaveUeNetDevice> mmuedev = node->GetDevice(j)->GetObject<MmWaveUeNetDevice>();
      if (mcuedev) {
          imsi = int(mcuedev->GetImsi());
          int serving_cell = ue_assoc_list[imsi - 1];
          if (serving_cell==0){
              serving_cell=1;
            }
          Ptr<MobilityModel> model = node->GetObject<MobilityModel> ();
          Vector position = model->GetPosition ();
          NS_LOG_UNCOND ("Position of UE with IMSI " << imsi << " is " << model->GetPosition ()
                                                     << " at time "
                                                     << Simulator::Now ().GetSeconds ()
                                                     << ", UE connected to Cell: " << serving_cell);
            outFile.open(filename.c_str(), std::ios_base::out | std::ios_base::app);
            if (!outFile.is_open()) {
                NS_LOG_ERROR("Can't open file " << filename);
                return;
            }

          outFile << timestamp << "," << imsi << "," << position.x << "," << position.y << ",mc,"
                  << serving_cell << "," << m_startTime << std::endl;
          outFile.close ();
        }
      else
        {
          //
        }
    }
}

void
EnergyConsumptionUpdate (int nodeIndex, std::string filename, double totaloldEnergyConsumption,
                         double totalnewEnergyConsumption)
{
  //std::cout << "mmWave cell " << nodeIndex+2 << ": Total Energy Consumption " << totalnewEnergyConsumption << "J" << std::endl;
  Time currentTime = Simulator::Now ();
  std::ofstream outFile;
  outFile.open (filename, std::ios_base::out | std::ios_base::app);
  outFile << currentTime.GetSeconds () << "," << totalnewEnergyConsumption << ","
          << (totalnewEnergyConsumption - totaloldEnergyConsumption) << std::endl;
  totalnewEnergyConsumption_storage[nodeIndex] = totalnewEnergyConsumption;
}

void
EnergyConsumptionPrint (int nodeIndex)
{
  NS_LOG_UNCOND ("Total energy consumption for mmWave cell "
                 << nodeIndex + 2 << ": " << totalnewEnergyConsumption_storage[nodeIndex] << "J"
                 << " at time " << Simulator::Now ().GetSeconds ()
                 << ", diff from last measurement is: "
                 << (totalnewEnergyConsumption_storage[nodeIndex] -
                     totaloldEnergyConsumption_storage[nodeIndex])
                 << "J");
  totalnewEnergyConsumption_storage[nodeIndex] = totalnewEnergyConsumption_storage[nodeIndex];
  current_energy_consumption[nodeIndex] =
      totalnewEnergyConsumption_storage[nodeIndex] - totaloldEnergyConsumption_storage[nodeIndex];
  totaloldEnergyConsumption_storage[nodeIndex] = totalnewEnergyConsumption_storage[nodeIndex];
}

void
EnergyConsumptionSnapshot (int nodeIndex, std::string filename,
                           Ptr<energy::DeviceEnergyModel> model,
                           Ptr<MmWaveEnbNetDevice> enb)
{
  const double totalEnergy = model->GetTotalEnergyConsumption ();
  const double previousEnergy = totalnewEnergyConsumption_storage[nodeIndex];
  std::ofstream outFile;
  outFile.open (filename, std::ios_base::out | std::ios_base::app);
  if (!outFile.is_open ())
    {
      NS_LOG_ERROR ("Can't open file " << filename);
      return;
    }
  Ptr<MmWaveRadioEnergyModelEnb> nativeModel = DynamicCast<MmWaveRadioEnergyModelEnb> (model);
  if (nativeModel == nullptr)
    {
      NS_LOG_ERROR ("Native mmWave energy model unavailable for cell " << nodeIndex + 2);
      outFile.close ();
      return;
    }
  const uint16_t tasamPowerPercent = enb == nullptr
                                         ? static_cast<uint16_t> (std::lround (nativeModel->GetTxPowerPercent ()))
                                         : enb->GetTasamTxPowerPercent ();
  const uint64_t powerTransaction = enb == nullptr
                                        ? 0
                                         : enb->GetTasamPowerTransaction ();
  const uint16_t modelPowerPercent = static_cast<uint16_t> (
      std::lround (nativeModel->GetTxPowerPercent ()));
  // Emit one complete CSV record.  The previous implementation wrote the
  // first three columns, terminated the line, then appended the remaining
  // columns to a second line, which made the native corpus unreadable.
  outFile << Simulator::Now ().GetSeconds () << "," << totalEnergy << ","
          << (totalEnergy - previousEnergy) << "," << nativeModel->GetIdleTimeSeconds () << ","
          << nativeModel->GetTxTimeSeconds () << "," << nativeModel->GetDataTimeSeconds () << ","
          << nativeModel->GetCtrlTimeSeconds () << "," << tasamPowerPercent << ","
          << (nativeModel->IsCellOff () ? 0 : 1) << "," << powerTransaction << ","
          << modelPowerPercent << "," << tasamPowerPercent << ","
          << (enb != nullptr && enb->IsTasamPowerLeaseFresh () ? 1 : 0)
          << std::endl;
  totalnewEnergyConsumption_storage[nodeIndex] = totalEnergy;
  current_energy_consumption[nodeIndex] = totalEnergy - totaloldEnergyConsumption_storage[nodeIndex];
  totaloldEnergyConsumption_storage[nodeIndex] = totalEnergy;
}

void
TasamControlSnapshot (NetDeviceContainer devices, std::string filename,
                      double intervalSeconds, double stopSeconds)
{
  std::ofstream outFile;
  outFile.open (filename, std::ios_base::out | std::ios_base::app);
  if (!outFile.is_open ())
    {
      NS_LOG_ERROR ("Can't open file " << filename);
      return;
    }
  for (uint32_t index = 0; index < devices.GetN (); ++index)
    {
      Ptr<MmWaveEnbNetDevice> enb = DynamicCast<MmWaveEnbNetDevice> (devices.Get (index));
      if (enb == nullptr)
        {
          continue;
        }
      // Apply E2 requests on the ns-3 thread.  The E2 termination callback
      // only acknowledges/enqueues requests from its transport thread; the
      // native snapshot is the first independent evidence of radio change.
      enb->ProcessPendingTasamControls ();
      // The native trace remains the confirmation boundary for control.
      // Relink marker for the local E2Sim control path.
      Ptr<MmWaveEnbPhy> phy = enb->GetPhy ();
      const double observedTxPowerDbm = phy == nullptr ? 0.0 : phy->GetTxPower ();
      const std::string sourceGeneration = NativeSourceGeneration ();
      const uint64_t powerTransaction = enb->GetTasamPowerTransaction ();
      const uint64_t schedulerTransaction = enb->GetActiveTasamTransaction ();
      const uint64_t controlTransaction = schedulerTransaction > powerTransaction
                                              ? schedulerTransaction
                                              : powerTransaction;
      NativeControlContext context;
      ReadNativeControlContext (controlTransaction, context);
      const std::string evidenceCampaign = context.campaign.empty ()
                                                ? enb->GetCampaignId ()
                                                : context.campaign;
      const std::string evidenceGeneration = context.generation.empty ()
                                                 ? sourceGeneration
                                                 : context.generation;
      const uint64_t evidenceSequence = context.sequence > 0
                                             ? context.sequence
                                             : controlTransaction;
      // Keep PHY power readback separate from scheduler policy state.  The
      // Data Lake requires both records for the same power transaction before
      // it can confirm an economic action; the state snapshot alone is not an
      // ACK or proof of application.
      // PolicyActive is part of the native confirmation contract.  A
      // periodic snapshot with a hard-coded zero makes every otherwise valid
      // E2 readback look like a shadow/safety action to the delayed feedback
      // joiner.  Export the scheduler's actual atomic-policy state for both
      // rows so power and policy evidence can be correlated truthfully.
      const int policyActive = enb->GetActiveTasamTransaction () > 0 ? 1 : 0;
      outFile << Simulator::Now ().GetSeconds () << "," << enb->GetCellId () << ","
              << schedulerTransaction << ","
              << powerTransaction << ","
              << enb->GetActiveTasamUeCount () << ","
              << enb->GetTasamTxPowerPercent () << "," << observedTxPowerDbm << ","
              << enb->GetTasamNominalTxPowerDbm () << ",power_readback," << policyActive << ","
              << enb->GetTasamPowerExpirySimTime () << "," << sourceGeneration << ","
              << enb->GetAssociationEpoch () << ","
              << enb->GetActiveTasamAllocatedDlSymbols () << ","
              << enb->GetActiveTasamDlSymbolCapacity () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbolsBp () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbolsBp () << ","
              << enb->GetActiveTasamMandatoryDlSymbols () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbols () << ","
              << enb->GetActiveTasamWithheldDlSymbols () << ","
              << context.sleepTransactionId << ","
              << evidenceCampaign << ","
              << NativeEvidenceVersion () << ","
              << evidenceGeneration << "," << context.decisionId << ","
              << context.correlation << "," << evidenceSequence << ","
              << (g_nativeAggregatedEvidenceEnabled ? "tasam_native_aggregate_v1" : "legacy_native")
              << "," << (enb->IsTasamPowerLeaseFresh () ? 1 : 0)
              << std::endl;
      NativeControlContext schedulerContext;
      ReadNativeControlContext (schedulerTransaction > 0 ? schedulerTransaction : powerTransaction,
                                schedulerContext);
      const std::string schedulerCampaign = schedulerContext.campaign.empty ()
                                                 ? enb->GetCampaignId ()
                                                 : schedulerContext.campaign;
      const std::string schedulerGeneration = schedulerContext.generation.empty ()
                                                  ? sourceGeneration
                                                  : schedulerContext.generation;
      const uint64_t schedulerSequence = schedulerContext.sequence > 0
                                              ? schedulerContext.sequence
                                              : (schedulerTransaction > 0 ? schedulerTransaction
                                                                            : powerTransaction);
      outFile << Simulator::Now ().GetSeconds () << "," << enb->GetCellId () << ","
              << schedulerTransaction << ","
              << powerTransaction << ","
              << enb->GetActiveTasamUeCount () << ","
              << enb->GetTasamTxPowerPercent () << ","
              << observedTxPowerDbm << ","
              << enb->GetTasamNominalTxPowerDbm () << ",state_snapshot," << policyActive << ","
              << enb->GetTasamPowerExpirySimTime () << "," << sourceGeneration << ","
              << enb->GetAssociationEpoch () << ","
              << enb->GetActiveTasamAllocatedDlSymbols () << ","
              << enb->GetActiveTasamDlSymbolCapacity () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbolsBp () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbolsBp () << ","
              << enb->GetActiveTasamMandatoryDlSymbols () << ","
              << enb->GetActiveTasamDiscretionaryDlSymbols () << ","
              << enb->GetActiveTasamWithheldDlSymbols () << ","
              << schedulerContext.sleepTransactionId << ","
              << schedulerCampaign << ","
              << NativeEvidenceVersion () << ","
              << schedulerGeneration << "," << schedulerContext.decisionId << ","
              << schedulerContext.correlation << "," << schedulerSequence << ","
              << (g_nativeAggregatedEvidenceEnabled ? "tasam_native_aggregate_v1" : "legacy_native")
              << "," << (enb->IsTasamPowerLeaseFresh () ? 1 : 0)
              << std::endl;
    }
  const double next = Simulator::Now ().GetSeconds () + intervalSeconds;
  if (next <= stopSeconds + 1e-9)
    {
      Simulator::Schedule (Seconds (intervalSeconds), &TasamControlSnapshot,
                           devices, filename, intervalSeconds, stopSeconds);
    }
}

void
TasamAssociationSnapshot (NetDeviceContainer devices, std::string filename,
                          double intervalSeconds, double stopSeconds)
{
  std::ofstream outFile (filename, std::ios_base::out | std::ios_base::app);
  if (!outFile.is_open ())
    {
      NS_LOG_ERROR ("Can't open association trace " << filename);
      return;
    }
  // Association state is emitted only when a cell's mapping changes.  The
  // previous implementation rewrote all 20 IMSIs for every 100 ms tick,
  // creating unnecessary I/O while the mapping was stable.
  std::map<uint16_t, std::string> currentRows;
  for (uint32_t index = 0; index < devices.GetN (); ++index)
    {
      Ptr<MmWaveEnbNetDevice> enb = DynamicCast<MmWaveEnbNetDevice> (devices.Get (index));
      if (enb == nullptr || enb->GetRrc () == nullptr)
        {
          continue;
        }
      const uint64_t associationPowerTransaction = enb->GetTasamPowerTransaction ();
      const uint64_t associationSchedulerTransaction = enb->GetActiveTasamTransaction ();
      const uint64_t associationTransaction = associationPowerTransaction > 0
                                                   ? associationPowerTransaction
                                                   : associationSchedulerTransaction;
      NativeControlContext associationContext;
      ReadNativeControlContext (associationTransaction, associationContext);
      std::ostringstream rows;
      uint32_t attachedUeCount = 0;
      for (uint64_t imsi = 1; imsi <= 20; ++imsi)
        {
          const uint16_t rnti = enb->GetRrc ()->GetRntiFromImsi (imsi);
          if (rnti == 0)
            {
              continue;
            }
          rows << rnti << ":" << imsi << ";";
          ++attachedUeCount;
        }
      currentRows[enb->GetCellId ()] = rows.str ();
      if (currentRows[enb->GetCellId ()] != g_lastAssociationSignature[enb->GetCellId ()])
        {
          const std::string associationEpoch =
              "rrc-epoch-" + std::to_string (++g_associationEpoch[enb->GetCellId ()]);
          for (uint64_t imsi = 1; imsi <= 20; ++imsi)
            {
              const uint16_t rnti = enb->GetRrc ()->GetRntiFromImsi (imsi);
              if (rnti == 0)
                {
                  continue;
                }
              outFile << Simulator::Now ().GetSeconds () << "," << enb->GetCellId () << ","
                      << rnti << "," << imsi << "," << associationEpoch << ","
                      << NativeEvidenceVersion () << "," << enb->GetCampaignId () << ","
                      << NativeSourceGeneration () << "," << associationTransaction << ","
                      << (associationContext.sequence > 0 ? associationContext.sequence
                                                            : associationTransaction)
                      << "," << associationContext.decisionId << ","
                      << associationContext.correlation
                      << ",ue_association,1," << associationContext.sleepTransactionId
                      << std::endl;
            }
        }
      // An empty DU is meaningful only as a drain/sleep observation.  Export
      // an explicit per-DU snapshot even when the mapping is unchanged so the
      // collector can distinguish a genuinely empty source from a missing
      // association trace.
      const std::string snapshotEpoch =
          "rrc-snapshot-" + std::to_string (g_associationEpoch[enb->GetCellId ()]);
      outFile << Simulator::Now ().GetSeconds () << "," << enb->GetCellId () << ",,,"
              << snapshotEpoch << "," << NativeEvidenceVersion () << ","
              << enb->GetCampaignId () << "," << NativeSourceGeneration () << ","
              << associationTransaction << ","
              << (associationContext.sequence > 0 ? associationContext.sequence
                                                    : associationTransaction)
              << "," << associationContext.decisionId << ","
              << associationContext.correlation << ",cell_snapshot," << attachedUeCount
              << "," << associationContext.sleepTransactionId << std::endl;
    }
  g_lastAssociationSignature = std::move (currentRows);
  const double next = Simulator::Now ().GetSeconds () + intervalSeconds;
  if (next <= stopSeconds + 1e-9)
    {
      Simulator::Schedule (Seconds (intervalSeconds), &TasamAssociationSnapshot,
                           devices, filename, intervalSeconds, stopSeconds);
    }
}

// Periodic, campaign-local GBR accounting.  Counters are deltas taken from
// the native scheduler state; a configured bearer is therefore never treated
// as proof that its reservation fitted in the available radio capacity.
void
VehicleSchedulerSnapshot (NetDeviceContainer devices, std::string filename,
                          double intervalSeconds, double stopSeconds,
                          uint64_t configuredGbrDlBps)
{
  std::ofstream outFile (filename, std::ios_base::out | std::ios_base::app);
  if (!outFile.is_open ())
    {
      NS_LOG_ERROR ("Can't open vehicle scheduler trace " << filename);
      return;
    }
  for (uint32_t index = 0; index < devices.GetN (); ++index)
    {
      Ptr<MmWaveEnbNetDevice> enb = DynamicCast<MmWaveEnbNetDevice> (devices.Get (index));
      if (enb == nullptr || enb->GetRrc () == nullptr)
        {
          continue;
        }
      Ptr<MmWaveFlexTtiMacScheduler> scheduler;
      for (const auto& carrier : enb->GetCcMap ())
        {
          Ptr<MmWaveComponentCarrierEnb> cc =
              DynamicCast<MmWaveComponentCarrierEnb> (carrier.second);
          if (cc != nullptr)
            {
              scheduler = DynamicCast<MmWaveFlexTtiMacScheduler> (cc->GetMacScheduler ());
              if (scheduler != nullptr)
                {
                  break;
                }
            }
        }
      if (scheduler == nullptr)
        {
          continue;
        }
      // In MC, the LTE/EPC bearer can be configured before the secondary
      // mmWave RNTI exists.  Reconcile the canonical vehicle bearers on every
      // snapshot so attach/handover-created RNTIs receive the same GBR
      // contract without fabricating allocation results.
      if (configuredGbrDlBps > 0)
        {
          for (uint64_t candidate = 16; candidate <= 20; ++candidate)
            {
              const uint16_t rnti = enb->GetRrc ()->GetRntiFromImsi (candidate);
              scheduler->EnsureVehicleGbrReservation (rnti, configuredGbrDlBps);
            }
        }
      for (const auto& row : scheduler->GetVehicleGbrTelemetry ())
        {
          uint64_t imsi = 0;
          for (uint64_t candidate = 16; candidate <= 20; ++candidate)
            {
              if (enb->GetRrc ()->GetRntiFromImsi (candidate) == row.rnti)
                {
                  imsi = candidate;
                  break;
                }
            }
          outFile << Simulator::Now ().GetSeconds () << "," << enb->GetCellId () << ","
                      << row.rnti << "," << imsi << ","
                      << static_cast<uint32_t> (row.cqi) << ","
                  << static_cast<uint32_t> (row.mcs) << "," << row.queueBytes << ","
                  << row.gbrDlBps << "," << row.creditBytes << "," << row.requestedSymbols
                  << "," << row.grantedSymbols << "," << row.grantedTbBytes << ","
                  << row.harqNacks << "," << row.harqMaxRetxDrops << ","
                  << row.harqRetxSymbols << ","
                  << (row.capacityShortfall
                          ? (row.shortfallReason.empty() ? "scheduler_capacity_shortfall" : row.shortfallReason)
                          : "ok")
                  << std::endl;
        }
    }
  const double next = Simulator::Now ().GetSeconds () + intervalSeconds;
  if (next <= stopSeconds + 1e-9)
    {
      Simulator::Schedule (Seconds (intervalSeconds), &VehicleSchedulerSnapshot,
                           devices, filename, intervalSeconds, stopSeconds,
                           configuredGbrDlBps);
    }
}

void
ApplyFixedTasamPower (NetDeviceContainer devices, uint32_t powerPercent, uint32_t activeCells)
{
  for (uint32_t index = 0; index < devices.GetN (); ++index)
    {
      Ptr<MmWaveEnbNetDevice> enb = DynamicCast<MmWaveEnbNetDevice> (devices.Get (index));
      if (enb != nullptr)
        {
          const bool active = index < activeCells;
          if (active)
            {
              enb->SetTasamTxPowerPercent (enb->GetCellId (), powerPercent);
            }
          Ptr<MmWaveRadioEnergyModelEnb> energy = enb->GetObject<MmWaveRadioEnergyModelEnb> ();
          if (energy != nullptr)
            {
              energy->SetCellOff (!active);
            }
          if (!active && enb->GetPhy () != nullptr)
            {
              enb->GetPhy ()->SetTxPower (0.0);
              enb->GetPhy ()->SetNoiseFigure (100.0);
            }
        }
    }
}

static ns3::GlobalValue g_bufferSize("bufferSize", "RLC tx buffer size (MB)",
                                      ns3::UintegerValue(10),
                                      ns3::MakeUintegerChecker<uint32_t>());

static ns3::GlobalValue g_enableTraces("enableTraces", "If true, generate ns-3 traces",
                                        ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_nativeMinimalTraces(
    "nativeMinimalTraces",
    "Keep only scheduler/RLC/PDCP/MC traces needed by the native evidence contract",
    ns3::BooleanValue(false), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_nativeAggregatedEvidence(
    "nativeAggregatedEvidence",
    "Use compact native PDCP/control/allocation evidence without raw scheduler dumps",
    ns3::BooleanValue(false), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_nativeEvidencePeriodMs(
    "nativeEvidencePeriodMs",
    "Period of compact native control/allocation observations in milliseconds",
    ns3::UintegerValue(500),
    ns3::MakeUintegerChecker<uint32_t>(100, 5000));

static void
EnableScenarioTraces (Ptr<MmWaveHelper> helper, bool minimal, bool aggregated)
{
  if (aggregated)
    {
      // PDCP is the only high-volume trace required for real SLA evidence.
      // Native control snapshots provide the applied allocation/power state.
      helper->EnablePdcpTraces ();
      return;
    }
  if (minimal)
    {
      helper->EnableEnbSchedTrace ();
      helper->EnableRlcTraces ();
      helper->EnablePdcpTraces ();
      helper->EnableMcTraces ();
      return;
    }
  helper->EnableTraces ();
}

static ns3::GlobalValue g_enableVerboseRuntimeLogging(
    "enableVerboseRuntimeLogging",
    "If true, print per-step UE position and energy logs to stdout",
    ns3::BooleanValue(false),
    ns3::MakeBooleanChecker());

static ns3::GlobalValue g_enablePositionCsvDump(
    "enablePositionCsvDump",
    "If true, dump per-step UE/enb position helper files",
    ns3::BooleanValue(false),
    ns3::MakeBooleanChecker());

static ns3::GlobalValue g_enableEnergyCsvDump(
    "enableEnergyCsvDump",
    "If true, dump per-step cell energy CSV files",
    ns3::BooleanValue(false),
    ns3::MakeBooleanChecker());

static ns3::GlobalValue g_energyOutputDir(
    "energyOutputDir",
    "Directory for native per-cell energy CSV files",
    ns3::StringValue(""),
    ns3::MakeStringChecker());

static ns3::GlobalValue g_fixedTxPowerPercent(
    "fixedTxPowerPercent",
    "Initial fixed TA-SAM power percentage for calibration runs",
    ns3::UintegerValue(100),
    ns3::MakeUintegerChecker<uint32_t>(25, 100));

static ns3::GlobalValue g_activeCells(
    "activeCells",
    "Number of active cells in a fixed three-DU calibration run",
    ns3::UintegerValue(3),
    ns3::MakeUintegerChecker<uint32_t>(1, 3));

static ns3::GlobalValue g_e2lteEnabled("e2lteEnabled", "If true, send LTE E2 reports",
                                        ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2nrEnabled("e2nrEnabled", "If true, send NR E2 reports",
                                       ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2ControlEnabled(
    "e2ControlEnabled", "If true, enable E2 RC control independently of report modes",
    ns3::BooleanValue(false), ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2du("e2du", "If true, send DU reports", ns3::BooleanValue(true),
                                ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2cuUp("e2cuUp", "If true, send CU-UP reports", ns3::BooleanValue(true),
                                  ns3::MakeBooleanChecker());

static ns3::GlobalValue g_e2cuCp("e2cuCp", "If true, send CU-CP reports", ns3::BooleanValue(true),
                                  ns3::MakeBooleanChecker());

static ns3::GlobalValue g_reducedPmValues("reducedPmValues", "If true, use a subset of the the pm containers",
    ns3::BooleanValue(true), ns3::MakeBooleanChecker());

static ns3::GlobalValue
    g_hoSinrDifference("hoSinrDifference",
                        "The value for which an handover between MmWave eNB is triggered",
                        ns3::DoubleValue(3), ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue
    g_indicationPeriodicity("indicationPeriodicity",
                             "E2 Indication Periodicity reports (value in seconds)",
                             ns3::DoubleValue(0.1), ns3::MakeDoubleChecker<double>(0.01, 2.0));

static ns3::GlobalValue g_simTime("simTime", "Simulation time in seconds", ns3::DoubleValue(1000),
                                   ns3::MakeDoubleChecker<double>(0.1, 500000.0));

static ns3::GlobalValue g_outageThreshold("outageThreshold",
                                           "SNR threshold for outage events [dB]", // use -1000.0 with NoAuto
                                           ns3::DoubleValue(-5.0),
                                           ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue g_numberOfRaPreambles(
    "numberOfRaPreambles",
    "how many random access preambles are available for the contention based RACH process",
    ns3::UintegerValue(40), // Indicated for TS use case, 52 is default
    ns3::MakeUintegerChecker<uint8_t>());

static ns3::GlobalValue
    g_handoverMode("handoverMode",
                    "HO euristic to be used,"
                    "can be only \"NoAuto\", \"FixedTtt\", \"DynamicTtt\",   \"Threshold\"",
                    ns3::StringValue("DynamicTtt"), ns3::MakeStringChecker());

static ns3::GlobalValue g_e2TermIp("e2TermIp", "The IP address of the RIC E2 termination",
                                    ns3::StringValue("127.0.0.1"), ns3::MakeStringChecker());

static ns3::GlobalValue g_e2TermPort(
    "e2TermPort", "SCTP port of the per-campaign RIC E2 termination",
    ns3::UintegerValue(36421), ns3::MakeUintegerChecker<uint16_t>());

static ns3::GlobalValue g_e2LocalPort(
    "e2LocalPort", "First local SCTP port reserved by this campaign",
    ns3::UintegerValue(38470), ns3::MakeUintegerChecker<uint16_t>());

static ns3::GlobalValue
        g_enableE2FileLogging("enableE2FileLogging",
                              "If true, generate offline file logging instead of connecting to RIC",
                              ns3::BooleanValue(false), ns3::MakeBooleanChecker());
static ns3::GlobalValue g_e2_func_id("KPM_E2functionID", "Function ID to subscribe",
                                      ns3::DoubleValue(2),
                                      ns3::MakeDoubleChecker<double>());
static ns3::GlobalValue g_rc_e2_func_id("RC_E2functionID", "Function ID to subscribe",
                                         ns3::DoubleValue(3),
                                         ns3::MakeDoubleChecker<double>());

static ns3::GlobalValue g_controlFileName("controlFileName",
                                           "The path to the control file (can be absolute)",
                                           ns3::StringValue(""),
                                           ns3::MakeStringChecker());

static ns3::GlobalValue g_ranPressureProfile(
    "ranPressureProfile",
    "Named offered-load profile applied over simulation time without changing UE counts",
    ns3::StringValue("none"),
    ns3::MakeStringChecker());

// TODO: running flags
static ns3::GlobalValue mmWave_nodes ("N_MmWaveEnbNodes", "Number of mmWaveNodes",
                                      ns3::UintegerValue (4),
                                      ns3::MakeUintegerChecker<uint8_t> ());
// TODO: next step(make it in correct way, regarding to position)
// static ns3::GlobalValue lteEnb_nodes ("N_LteEnbNodes", "Number of LteEnbNodes",
//                                       ns3::UintegerValue (1),
//                                       ns3::MakeUintegerChecker<uint8_t> ());

static ns3::GlobalValue ue_s ("N_Ues", "Number of User Equipments",
                              ns3::UintegerValue (3),
                              ns3::MakeUintegerChecker<uint32_t> ());

static ns3::GlobalValue center_freq ("CenterFrequency", "Center Frequency Value",
                                     ns3::DoubleValue (3.5e9),
                                     ns3::MakeDoubleChecker<double> ());

static ns3::GlobalValue bandwidth_value ("Bandwidth", "Bandwidth Value",
                                         ns3::DoubleValue (20e6),
                                         ns3::MakeDoubleChecker<double> ());
// TODO: check for later
// static ns3::GlobalValue num_antennas_McUe ("N_AntennasMcUe", "Number of Antenna as McUe",
//                                       ns3::IntegerValue (1),
//                                       ns3::MakeIntegerChecker<int> ());

// static ns3::GlobalValue num_antennas_MmWave ("N_AntennasMmWave", "Number of Antenna as MmWave",
//                                       ns3::IntegerValue (1),
//                                       ns3::MakeIntegerChecker<int> ());

static ns3::GlobalValue interside_distance_value_ue ("IntersideDistanceUEs", "Interside Distance Value",
                                      ns3::DoubleValue (500),
                                      ns3::MakeDoubleChecker<double> ());
static ns3::GlobalValue interside_distance_value_cell ("IntersideDistanceCells", "Interside Distance Value",
                                                  ns3::DoubleValue (600),
                                                  ns3::MakeDoubleChecker<double> ());

namespace {

struct RanPressureStage
{
  std::string name;
  double durationSeconds;
  double cameraRateMultiplier;
  double backgroundRateMultiplier;
  double vehicleRateMultiplier;
};

static std::vector<RanPressureStage>
BuildRanPressureStages(const std::string& profileName)
{
  if (profileName.empty() || profileName == "none")
    {
      return {};
    }

  if (profileName == "drl_article_v1")
    {
      return {
          {"baseline_healthy", 120.0, 1.0, 1.0, 1.0},
          {"app1_warning", 70.0, 2.2, 1.0, 1.0},
          {"app1_guard", 40.0, 4.0, 1.05, 1.1},
          {"baseline_recovery_after_app1", 90.0, 1.2, 1.0, 1.0},
          {"app2_stressed_safe", 90.0, 1.15, 1.55, 1.15},
          {"vehicle_stressed_safe", 90.0, 1.4, 1.25, 2.4},
          {"app2_guard", 45.0, 1.0, 2.1, 1.1},
          {"baseline_recovery_after_app2", 120.0, 1.1, 1.0, 1.0},
          {"vehicle_warning", 45.0, 1.35, 1.5, 3.2},
          {"baseline_recovery_after_vehicle", 120.0, 1.1, 1.0, 1.0},
          {"app2_critical", 30.0, 1.3, 2.6, 1.2},
          {"app1_critical_short", 25.0, 5.0, 1.3, 1.3},
          {"baseline_recovery_after_critical", 150.0, 1.0, 1.0, 1.0},
      };
    }

  if (profileName == "tasam_training_balanced_v1" ||
      profileName == "tasam_training_balanced_v2")
    {
      return {
          {"allowed_bootstrap", 4.0, 1.0, 1.0, 1.0},
          {"allowed_stable", 4.0, 1.0, 0.95, 0.95},
          {"camera_conditional", 4.0, 0.8, 1.0, 1.0},
          {"camera_blocked", 3.0, 0.64, 1.05, 1.1},
          {"vehicle_conditional", 4.0, 1.0, 1.1, 2.4},
          {"vehicle_blocked", 3.0, 0.95, 1.2, 4.0},
          {"app2_conditional", 4.0, 0.98, 6.0, 1.1},
          {"app2_blocked", 3.0, 0.95, 12.0, 1.2},
          {"allowed_recovery", 4.0, 1.0, 1.0, 1.0},
      };
    }

  // v3 is the controlled online curriculum used by the Python alternator.
  // It preserves v1's offered-load multipliers but gives every stage a
  // twelve-second native window so a decision and the following real-PDCP
  // snapshot can be paired.  Keep this mapping explicit: accepting the
  // Python profile name without matching its durations would make the
  // simulator and collector describe different training events.
  if (profileName == "tasam_training_balanced_v3" ||
      profileName == "tasam_training_balanced_v4_v2x" ||
      profileName == "tasam_training_balanced_v4_v2x_gbr" ||
      profileName == "tasam_training_balanced_v4_v2x_gbr_priority" ||
      profileName == "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc" ||
      profileName == "tasam_training_balanced_v5_v2x_gbr_deadline_mc" ||
      profileName == "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback" ||
      profileName == "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max")
    {
      return {
          {"allowed_bootstrap", 12.0, 1.0, 1.0, 1.0},
          {"allowed_stable", 12.0, 1.0, 0.95, 0.95},
          {"camera_conditional", 12.0, 0.8, 1.0, 1.0},
          {"camera_blocked", 12.0, 0.64, 1.05, 1.1},
          {"vehicle_conditional", 12.0, 1.0, 1.1, 2.4},
          {"vehicle_blocked", 12.0, 0.95, 1.2, 4.0},
          {"app2_conditional", 12.0, 0.98, 6.0, 1.1},
          {"app2_blocked", 12.0, 0.95, 12.0, 1.2},
          {"allowed_recovery", 12.0, 1.0, 1.0, 1.0},
      };
    }

  // The economic profiles are deliberately kept in the native ns-3
  // scenario as well as in collection_event_alternator.py.  Previously the
  // Python side accepted tasam_training_economic_v4 while ns-3 silently
  // disabled its offered-load schedule, making the two sides incomparable.
  // Vehicle traffic is never increased by this profile: autonomous-vehicle
  // safety is exercised through the real PDCP path and the dedicated bearer,
  // not by fabricating an overload in the simulator.
  if (profileName == "tasam_training_economic_v4" ||
      profileName == "tasam_training_economic_vehicle_safe_v1")
    {
      return {
          {"allowed_bootstrap", 24.0, 1.0, 1.0, 1.0},
          {"allowed_stable", 72.0, 1.0, 0.95, 1.0},
          {"camera_conditional", 48.0, 0.8, 1.0, 1.0},
          {"vehicle_conditional", 48.0, 1.0, 1.0, 1.0},
          {"app2_conditional", 48.0, 1.0, 1.0, 1.0},
          {"allowed_recovery", 72.0, 1.0, 1.0, 1.0},
      };
    }

  if (profileName == "drl_article_conflict_forced_v1")
    {
      return {
          {"baseline_healthy", 60.0, 1.0, 1.0},
          {"camera_overload", 90.0, 8.0, 1.2},
          {"mixed_overload", 120.0, 9.5, 4.5},
          {"background_overload", 90.0, 4.2, 6.5},
          {"recovery_window", 90.0, 1.15, 1.0},
          {"camera_overload_repeat", 60.0, 8.5, 1.4},
          {"final_recovery", 90.0, 1.0, 1.0},
      };
    }

  if (profileName == "drl_article_conflict_forced_fast_v1")
    {
      return {
          {"baseline_healthy", 10.0, 1.0, 1.0},
          {"camera_overload", 20.0, 8.0, 1.2},
          {"mixed_overload", 30.0, 9.5, 4.5},
          {"background_overload", 20.0, 4.2, 6.5},
          {"recovery_window", 15.0, 1.15, 1.0},
          {"camera_overload_repeat", 15.0, 8.5, 1.4},
          {"final_recovery", 20.0, 1.0, 1.0},
      };
    }

  if (profileName == "drl_article_conflict_smoke_v1")
    {
      return {
          {"baseline_healthy", 1.0, 1.0, 1.0},
          {"camera_overload", 8.0, 8.0, 1.2},
          {"mixed_overload", 8.0, 9.5, 4.5},
          {"background_overload", 6.0, 4.2, 6.5},
          {"recovery_window", 4.0, 1.15, 1.0},
          {"camera_overload_repeat", 6.0, 8.5, 1.4},
          {"final_recovery", 5.0, 1.0, 1.0},
      };
    }

  NS_LOG_UNCOND("[RAN_PRESSURE] profile '" << profileName << "' not recognized; disabling offered-load staging");
  return {};
}

static double
RanPressureCycleDuration(const std::vector<RanPressureStage>& stages)
{
  double total = 0.0;
  for (const auto& stage : stages)
    {
      total += stage.durationSeconds;
    }
  return total;
}

static uint32_t
ClampIntervalUs(double intervalUs)
{
  return static_cast<uint32_t>(std::max(50.0, std::round(intervalUs)));
}

static void
ApplyUdpClientShape(const Ptr<UdpClient>& client, uint32_t packetSizeBytes, uint32_t intervalUs)
{
  if (!client)
    {
      return;
    }

  client->SetAttribute("PacketSize", UintegerValue(packetSizeBytes));
  client->SetAttribute("Interval", TimeValue(MicroSeconds(intervalUs)));
}

static void
ApplyRanPressureStage(const RanPressureStage stage,
                      const std::vector<Ptr<UdpClient>> cameraClients,
                      const std::vector<Ptr<UdpClient>> backgroundClients,
                      const std::vector<Ptr<UdpClient>> vehicleClients,
                      uint32_t cameraBasePacketSizeBytes,
                      uint32_t cameraBaseIntervalUs,
                      uint32_t backgroundBasePacketSizeBytes,
                      uint32_t backgroundBaseIntervalUs,
                      uint32_t vehicleBasePacketSizeBytes,
                      uint32_t vehicleBaseIntervalUs,
                      uint32_t cycleIndex)
{
  uint32_t cameraIntervalUs =
      ClampIntervalUs(cameraBaseIntervalUs / std::max(stage.cameraRateMultiplier, 0.1));
  uint32_t backgroundIntervalUs =
      ClampIntervalUs(backgroundBaseIntervalUs / std::max(stage.backgroundRateMultiplier, 0.1));
  uint32_t vehicleIntervalUs =
      ClampIntervalUs(vehicleBaseIntervalUs / std::max(stage.vehicleRateMultiplier, 0.1));

  for (const auto& client : cameraClients)
    {
      ApplyUdpClientShape(client, cameraBasePacketSizeBytes, cameraIntervalUs);
    }

  for (const auto& client : backgroundClients)
    {
      ApplyUdpClientShape(client, backgroundBasePacketSizeBytes, backgroundIntervalUs);
    }

  for (const auto& client : vehicleClients)
    {
      ApplyUdpClientShape(client, vehicleBasePacketSizeBytes, vehicleIntervalUs);
    }

  NS_LOG_UNCOND("[RAN_PRESSURE] cycle=" << cycleIndex
                                        << " stage=" << stage.name
                                        << " t=" << Simulator::Now().GetSeconds()
                                        << "s camera_multiplier=" << stage.cameraRateMultiplier
                                        << " background_multiplier="
                                        << stage.backgroundRateMultiplier
                                        << " vehicle_multiplier="
                                        << stage.vehicleRateMultiplier
                                        << " camera_interval_us=" << cameraIntervalUs
                                        << " background_interval_us=" << backgroundIntervalUs
                                        << " vehicle_interval_us=" << vehicleIntervalUs
                                        << " camera_packet_bytes=" << cameraBasePacketSizeBytes
                                        << " background_packet_bytes=" << backgroundBasePacketSizeBytes
                                        << " vehicle_packet_bytes=" << vehicleBasePacketSizeBytes);
}

static void
ScheduleRanPressureProfile(const std::vector<RanPressureStage>& stages,
                           const std::vector<Ptr<UdpClient>>& cameraClients,
                           const std::vector<Ptr<UdpClient>>& backgroundClients,
                           const std::vector<Ptr<UdpClient>>& vehicleClients,
                           uint32_t cameraBasePacketSizeBytes,
                           uint32_t cameraBaseIntervalUs,
                           uint32_t backgroundBasePacketSizeBytes,
                           uint32_t backgroundBaseIntervalUs,
                           uint32_t vehicleBasePacketSizeBytes,
                           uint32_t vehicleBaseIntervalUs,
                           double simTime)
{
  if (stages.empty())
    {
      return;
    }

  const double cycleDuration = RanPressureCycleDuration(stages);
  if (cycleDuration <= 0.0)
    {
      return;
    }

  double scheduleAt = 0.0;
  uint32_t cycleIndex = 0;
  while (scheduleAt < simTime)
    {
      for (const auto& stage : stages)
        {
          if (scheduleAt >= simTime)
            {
              break;
            }

          Simulator::Schedule(Seconds(scheduleAt),
                          &ApplyRanPressureStage,
                          stage,
                          cameraClients,
                          backgroundClients,
                          vehicleClients,
                          cameraBasePacketSizeBytes,
                          cameraBaseIntervalUs,
                          backgroundBasePacketSizeBytes,
                          backgroundBaseIntervalUs,
                          vehicleBasePacketSizeBytes,
                          vehicleBaseIntervalUs,
                          cycleIndex);
          scheduleAt += stage.durationSeconds;
        }
      ++cycleIndex;
    }
}

} // namespace

int
main(int argc, char *argv[]) {
  LogComponentEnableAll(LOG_PREFIX_ALL);
  //  LogComponentEnable ("RicControlMessage", LOG_LEVEL_ALL);
  //  LogComponentEnable ("KpmIndication", LOG_LEVEL_DEBUG);
  //LogComponentEnable("KpmIndication", LOG_LEVEL_INFO);

  // LogComponentEnable ("Asn1Types", LOG_LEVEL_LOGIC);
  //   LogComponentEnable ("E2Termination", LOG_LEVEL_LOGIC);
  //  LogComponentEnable ("E2Termination", LOG_LEVEL_DEBUG);

  // LogComponentEnable ("LteEnbNetDevice", LOG_LEVEL_ALL);
  // LogComponentEnable ("MmWaveEnbNetDevice", LOG_LEVEL_INFO);
 //  LogComponentEnable ("LteEnbRrc", LOG_LEVEL_INFO);
 //LogComponentEnable ("EpcX2", LOG_LEVEL_LOGIC);

  

  // The maximum X coordinate of the scenario


  maxXAxis = 4000;
  // The maximum Y coordinate of the scenario
  maxYAxis = 4000;

  // Command line arguments
  uint32_t ueCount = 20;
  uint32_t cameraUeCount = 3;
  uint32_t vehicleUeCount = 5;
  uint32_t mmWaveEnbNodeCount = 4;
  double ueSpeedMin = 2.0;
  double ueSpeedMax = 4.0;
  double bearerStatsEpochMs = 100.0;
  double bandwidthMHz = 100.0;
  uint32_t cameraPacketSizeBytes = 1000;
  uint32_t cameraPacketIntervalUs = 320;
  uint32_t backgroundPacketSizeBytes = 128;
  uint32_t backgroundPacketIntervalUs = 10000;
  uint32_t vehiclePacketSizeBytes = 800;
  uint32_t vehiclePacketIntervalUs = 4000;
  bool enableTracesAfterAttach = false;
  bool useMcUeDevices = true;
  CommandLine cmd;
  cmd.AddValue("ueCount", "Total number of UE nodes to instantiate", ueCount);
  cmd.AddValue("cameraUeCount", "Number of camera UEs from the start of the IMSI range", cameraUeCount);
  cmd.AddValue("vehicleUeCount", "Number of vehicle UEs reserved at the end of the IMSI range", vehicleUeCount);
  cmd.AddValue("mmWaveEnbNodes", "Number of mmWave eNB/gNB nodes", mmWaveEnbNodeCount);
  cmd.AddValue("ueSpeedMin", "Minimum UE mobility speed in m/s", ueSpeedMin);
  cmd.AddValue("ueSpeedMax", "Maximum UE mobility speed in m/s", ueSpeedMax);
  cmd.AddValue("bandwidthMHz", "mmWave carrier bandwidth in MHz", bandwidthMHz);
  cmd.AddValue("cameraPacketSizeBytes", "Camera downlink packet size in bytes", cameraPacketSizeBytes);
  cmd.AddValue("cameraPacketIntervalUs", "Camera downlink packet interval in microseconds", cameraPacketIntervalUs);
  cmd.AddValue("backgroundPacketSizeBytes", "Background downlink packet size in bytes", backgroundPacketSizeBytes);
  cmd.AddValue("backgroundPacketIntervalUs", "Background downlink packet interval in microseconds", backgroundPacketIntervalUs);
  cmd.AddValue("vehiclePacketSizeBytes", "Vehicle downlink packet size in bytes", vehiclePacketSizeBytes);
  cmd.AddValue("vehiclePacketIntervalUs", "Vehicle downlink packet interval in microseconds", vehiclePacketIntervalUs);
  cmd.AddValue("bearerStatsEpochMs",
               "Epoch duration for PDCP/RLC bearer stats files in milliseconds",
               bearerStatsEpochMs);
  cmd.AddValue("enableTracesAfterAttach", "Enable mmWave traces after UE attach instead of before", enableTracesAfterAttach);
  bool nativeAggregatedEvidence = false;
  uint32_t nativeEvidencePeriodMs = 500;
  cmd.AddValue("nativeAggregatedEvidence", "Use compact native evidence without raw scheduler/SINR/X2 traces", nativeAggregatedEvidence);
  cmd.AddValue("nativeEvidencePeriodMs", "Compact native evidence period in milliseconds", nativeEvidencePeriodMs);
  cmd.AddValue("useMcUeDevices", "Use LTE-anchored multi-connectivity UE devices instead of mmWave-only UE devices", useMcUeDevices);
  cmd.Parse(argc, argv);

  bool harqEnabled = true;

  UintegerValue uintegerValue;
  BooleanValue booleanValue;
  StringValue stringValue;
  DoubleValue doubleValue;

  GlobalValue::GetValueByName("hoSinrDifference", doubleValue);
  double hoSinrDifference = doubleValue.Get();
  GlobalValue::GetValueByName("bufferSize", uintegerValue);
  uint32_t bufferSize = uintegerValue.Get();
  GlobalValue::GetValueByName("enableTraces", booleanValue);
  bool enableTraces = booleanValue.Get();
  GlobalValue::GetValueByName("nativeMinimalTraces", booleanValue);
  bool nativeMinimalTraces = booleanValue.Get();
  // These two options are command-line options owned by this scenario.  Do
  // not overwrite the parsed values with the GlobalValue defaults here:
  // doing so silently disabled v9_fidelity even when the launcher passed
  // --nativeAggregatedEvidence=1.
  g_nativeAggregatedEvidenceEnabled = nativeAggregatedEvidence;
  UintegerValue nativeEvidencePeriodValue;
  GlobalValue::GetValueByName("outageThreshold", doubleValue);
  double outageThreshold = doubleValue.Get();
  GlobalValue::GetValueByName("handoverMode", stringValue);
  std::string handoverMode = stringValue.Get();
  GlobalValue::GetValueByName("e2TermIp", stringValue);
  std::string e2TermIp = stringValue.Get();
  GlobalValue::GetValueByName("e2TermPort", uintegerValue);
  uint16_t e2TermPort = static_cast<uint16_t>(uintegerValue.Get());
  GlobalValue::GetValueByName("e2LocalPort", uintegerValue);
  uint16_t e2LocalPort = static_cast<uint16_t>(uintegerValue.Get());
  GlobalValue::GetValueByName("enableE2FileLogging", booleanValue);
  bool enableE2FileLogging = booleanValue.Get();
  GlobalValue::GetValueByName("KPM_E2functionID", doubleValue);
  double g_e2_func_id = doubleValue.Get();
  GlobalValue::GetValueByName("RC_E2functionID", doubleValue);
  double g_rc_e2_func_id = doubleValue.Get();


  GlobalValue::GetValueByName("numberOfRaPreambles", uintegerValue);
  uint8_t numberOfRaPreambles = uintegerValue.Get();

    NS_LOG_UNCOND("bufferSize " << bufferSize << " OutageThreshold " << outageThreshold
                                << " HandoverMode " << handoverMode << " e2TermIp " << e2TermIp
                                << " e2TermPort " << e2TermPort << " e2LocalPort " << e2LocalPort
                                << " enableE2FileLogging " << enableE2FileLogging
                                << " E2 Function ID " << g_e2_func_id);

  GlobalValue::GetValueByName("e2lteEnabled", booleanValue);
  bool e2lteEnabled = booleanValue.Get();
  GlobalValue::GetValueByName("e2nrEnabled", booleanValue);
  bool e2nrEnabled = booleanValue.Get();
  GlobalValue::GetValueByName("e2ControlEnabled", booleanValue);
  bool e2ControlEnabled = booleanValue.Get();
  GlobalValue::GetValueByName("e2du", booleanValue);
  bool e2du = booleanValue.Get();
  GlobalValue::GetValueByName("e2cuUp", booleanValue);
  bool e2cuUp = booleanValue.Get();
  GlobalValue::GetValueByName("e2cuCp", booleanValue);
  bool e2cuCp = booleanValue.Get();

  GlobalValue::GetValueByName("reducedPmValues", booleanValue);
  bool reducedPmValues = booleanValue.Get();

  GlobalValue::GetValueByName("indicationPeriodicity", doubleValue);
  double indicationPeriodicity = doubleValue.Get();
  GlobalValue::GetValueByName("controlFileName", stringValue);
  std::string controlFilename = stringValue.Get();
  GlobalValue::GetValueByName("ranPressureProfile", stringValue);
  std::string ranPressureProfile = stringValue.Get();
  StringValue configuredEvidenceDir;
  GlobalValue::GetValueByName("energyOutputDir", configuredEvidenceDir);
  const std::string nativeEvidenceDir = configuredEvidenceDir.Get();
  const auto nativeEvidencePath = [&nativeEvidenceDir](const std::string& name) {
    return nativeEvidenceDir.empty() ? name : nativeEvidenceDir + "/" + name;
  };

  NS_LOG_UNCOND("e2lteEnabled " << e2lteEnabled << " e2nrEnabled " << e2nrEnabled
                                 << " e2ControlEnabled " << e2ControlEnabled << " e2du "
                                 << e2du << " e2cuCp " << e2cuCp << " e2cuUp " << e2cuUp
                                 << " controlFilename " << controlFilename
                                 << " indicationPeriodicity " << indicationPeriodicity
                                 << " ranPressureProfile " << ranPressureProfile);

  Config::SetDefault("ns3::LteEnbNetDevice::ControlFileName", StringValue(controlFilename));
  Config::SetDefault("ns3::LteEnbNetDevice::E2Periodicity", DoubleValue(indicationPeriodicity));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::E2Periodicity",
                      DoubleValue(indicationPeriodicity));

  Config::SetDefault("ns3::MmWaveHelper::E2ModeLte", BooleanValue(e2lteEnabled));
  Config::SetDefault("ns3::MmWaveHelper::E2ModeNr", BooleanValue(e2nrEnabled));
  Config::SetDefault("ns3::MmWaveHelper::E2Control", BooleanValue(e2ControlEnabled));

  // The DU PM reports should come from both NR gNB as well as LTE eNB,
  // since in the RLC/MAC/PHY entities are present in BOTH NR gNB as well as LTE eNB.
  // DU reports from LTE eNB are not implemented in this release
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableDuReport", BooleanValue(e2du));

  // The CU-UP PM reports should only come from LTE eNB, since in the NS3 “EN-DC
  // simulation (Option 3A)”, the PDCP is only in the LTE eNB and NOT in the NR gNB
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuUpReport", BooleanValue(e2cuUp));
  Config::SetDefault("ns3::LteEnbNetDevice::EnableCuUpReport", BooleanValue(e2cuUp));

  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableCuCpReport", BooleanValue(e2cuCp));
  Config::SetDefault("ns3::LteEnbNetDevice::EnableCuCpReport", BooleanValue(e2cuCp));

  Config::SetDefault("ns3::MmWaveEnbNetDevice::ReducedPmValues", BooleanValue(reducedPmValues));
  Config::SetDefault("ns3::LteEnbNetDevice::ReducedPmValues", BooleanValue(reducedPmValues));

  Config::SetDefault("ns3::LteEnbNetDevice::EnableE2FileLogging",
                      BooleanValue(enableE2FileLogging && !e2ControlEnabled));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::EnableE2FileLogging",
                      BooleanValue(enableE2FileLogging && !e2ControlEnabled));


  Config::SetDefault("ns3::LteEnbNetDevice::KPM_E2functionID",
                      DoubleValue(g_e2_func_id));
  Config::SetDefault("ns3::MmWaveEnbNetDevice::KPM_E2functionID",
                      DoubleValue(g_e2_func_id));

  Config::SetDefault("ns3::LteEnbNetDevice::RC_E2functionID",
                      DoubleValue(g_rc_e2_func_id));

  Config::SetDefault("ns3::MmWaveEnbMac::NumberOfRaPreambles",
                      UintegerValue(numberOfRaPreambles));

  Config::SetDefault("ns3::MmWaveHelper::HarqEnabled", BooleanValue(harqEnabled));
  Config::SetDefault("ns3::MmWaveHelper::UseIdealRrc", BooleanValue(true));
  Config::SetDefault("ns3::MmWaveHelper::E2TermIp", StringValue(e2TermIp));
  Config::SetDefault("ns3::MmWaveHelper::E2Port", UintegerValue(e2TermPort));
  Config::SetDefault("ns3::MmWaveHelper::E2LocalPort", UintegerValue(e2LocalPort));

  Config::SetDefault("ns3::MmWaveFlexTtiMacScheduler::HarqEnabled", BooleanValue(harqEnabled));
  Config::SetDefault("ns3::MmWaveFlexTtiMacScheduler::VehicleGbrPriority",
                     BooleanValue(ranPressureProfile ==
                                  "tasam_training_balanced_v4_v2x_gbr_priority" ||
                                  ranPressureProfile ==
                                  "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc" ||
                                  ranPressureProfile ==
                                  "tasam_training_balanced_v5_v2x_gbr_deadline_mc" ||
                                  ranPressureProfile ==
                                  "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback" ||
                                  ranPressureProfile ==
                                  "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max" ||
                                  ranPressureProfile ==
                                  "tasam_training_economic_vehicle_safe_v1"));
  Config::SetDefault("ns3::MmWavePhyMacCommon::NumHarqProcess", UintegerValue(100));
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::EpochDuration",
                      TimeValue(MilliSeconds(bearerStatsEpochMs)));
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::DlPdcpOutputFilename",
                      StringValue(nativeEvidencePath("DlPdcpStats.txt")));
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::UlPdcpOutputFilename",
                      StringValue(nativeEvidencePath("UlPdcpStats.txt")));
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::DlRlcOutputFilename",
                      StringValue(nativeEvidencePath("DlRlcStats.txt")));
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::UlRlcOutputFilename",
                      StringValue(nativeEvidencePath("UlRlcStats.txt")));
  const bool v5VehicleProfile =
      ranPressureProfile == "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc" ||
      ranPressureProfile == "tasam_training_balanced_v5_v2x_gbr_deadline_mc";
  const bool safeVehicleProfile =
      ranPressureProfile == "tasam_training_economic_vehicle_safe_v1";
  const bool v6VehicleProfile =
      ranPressureProfile == "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback" ||
      ranPressureProfile == "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max";
  const bool strictVehicleProfile = v5VehicleProfile || v6VehicleProfile || safeVehicleProfile;
  Config::SetDefault("ns3::MmWaveBearerStatsCalculator::VehiclePdcpEventOutputFilename",
                      StringValue(strictVehicleProfile ? nativeEvidencePath("VehiclePdcpPduTrace.csv") : ""));

  // The compact v9 fidelity profile keeps only PDCP plus the native
  // aggregated control/allocation and energy evidence.  These helper
  // calculators are instantiated by the X2/PDCP plumbing and otherwise
  // open their legacy analytic files even when their callbacks are not part
  // of the evidence contract.  Redirect them explicitly so a fidelity run
  // cannot silently produce SINR/RLC/X2 dumps.
  if (nativeAggregatedEvidence)
    {
      Config::SetDefault("ns3::MmWaveBearerStatsConnector::MmWaveSinrOutputFilename",
                         StringValue(strictVehicleProfile ? nativeEvidencePath("VehicleLinkTrace.csv") : "/dev/null"));
      Config::SetDefault("ns3::MmWaveBearerStatsConnector::LteSinrOutputFilename",
                         StringValue("/dev/null"));
      Config::SetDefault("ns3::CoreNetworkStatsCalculator::X2FileName",
                         StringValue("/dev/null"));
      Config::SetDefault("ns3::CoreNetworkStatsCalculator::S1MmeFileName",
                         StringValue("/dev/null"));
      Config::SetDefault("ns3::MmWaveBearerStatsCalculator::DlRlcOutputFilename",
                         StringValue("/dev/null"));
      Config::SetDefault("ns3::MmWaveBearerStatsCalculator::UlRlcOutputFilename",
                         StringValue("/dev/null"));
    }

  // set to false to use the 3GPP radiation pattern (proper configuration of the bearing and downtilt angles is needed)
  Config::SetDefault("ns3::PhasedArrayModel::AntennaElement",
    PointerValue(CreateObject<IsotropicAntennaModel>()));
Config::SetDefault ("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue (MilliSeconds (100.0)));
Config::SetDefault ("ns3::ThreeGppChannelConditionModel::UpdatePeriod",
  TimeValue (MilliSeconds (100)));

  Config::SetDefault("ns3::LteRlcAm::ReportBufferStatusTimer", TimeValue(MilliSeconds(10.0)));
  Config::SetDefault("ns3::LteRlcUmLowLat::ReportBufferStatusTimer",
                      TimeValue(MilliSeconds(strictVehicleProfile ? 1.0 : 10.0)));
  Config::SetDefault("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue(bufferSize * 1024 * 1024));
  Config::SetDefault("ns3::LteRlcUmLowLat::MaxTxBufferSize",
                      UintegerValue(bufferSize * 1024 * 1024));
  Config::SetDefault("ns3::LteRlcAm::MaxTxBufferSize", UintegerValue(bufferSize * 1024 * 1024));

  Config::SetDefault("ns3::LteEnbRrc::OutageThreshold", DoubleValue(outageThreshold));
  Config::SetDefault("ns3::LteEnbRrc::SecondaryCellHandoverMode", StringValue(handoverMode));
  Config::SetDefault("ns3::LteEnbRrc::HoSinrDifference", DoubleValue(hoSinrDifference));
  // v5 historically used 100 ms here, longer than the V2X latency budget.
  // v6 evaluates MC association every 5 ms and uses a 5--15 ms dynamic TTT.
  Config::SetDefault("ns3::LteEnbRrc::CrtPeriod",
                     IntegerValue(v6VehicleProfile ? 5000 : 100000));
  if (v6VehicleProfile)
    {
      Config::SetDefault("ns3::LteEnbRrc::MinDynTttValue", UintegerValue(5));
      Config::SetDefault("ns3::LteEnbRrc::MaxDynTttValue", UintegerValue(15));
      Config::SetDefault("ns3::LteEnbRrc::AutoHandoverCooldown", IntegerValue(2000));
      Config::SetDefault("ns3::LteEnbRrc::AttachSinrMarginDb", IntegerValue(5));
      Config::SetDefault("ns3::LteEnbRrc::MmWaveReturnGuardMs", IntegerValue(100));
    }
  Config::SetDefault("ns3::ThreeGppPropagationLossModel::Frequency",DoubleValue(3.5e9));
  Config::SetDefault("ns3::ThreeGppPropagationLossModel::ShadowingEnabled",BooleanValue(false));
  // Carrier bandwidth in Hz
  double bandwidth = bandwidthMHz * 1e6;
  // Center frequency in Hz
  double centerFrequency = 3.5e9;
  // Distance between the mmWave BSs and the two co-located LTE and mmWave BSs in meters
  double isd_ue = 1000; // (interside distance)
  double isd_cell = 500; // (interside distance)

  // Number of antennas in each UE
  // GlobalValue::GetValueByName ("N_AntennasMcUe", uintegerValue);
  int numAntennasMcUe = 1; //uintegerValue.Get();
  // Number of antennas in each mmWave BS
  // GlobalValue::GetValueByName ("N_AntennasMmWave", uintegerValue);
  int numAntennasMmWave = 1; //uintegerValue.Get();

  NS_LOG_INFO("Bandwidth " << bandwidth << " centerFrequency " << double(centerFrequency)
                            << " isd_ue " << isd_ue << " numAntennasMcUe " << numAntennasMcUe
                            << " numAntennasMmWave " << numAntennasMmWave);

  // Set the number of antennas in the devices
  Config::SetDefault("ns3::McUeNetDevice::AntennaNum", UintegerValue(numAntennasMcUe));
  Config::SetDefault("ns3::MmWaveNetDevice::AntennaNum", UintegerValue(numAntennasMmWave));
  Config::SetDefault("ns3::MmWavePhyMacCommon::Bandwidth", DoubleValue(bandwidth));
  Config::SetDefault("ns3::MmWavePhyMacCommon::CenterFreq", DoubleValue(centerFrequency));

  Ptr <MmWaveHelper> mmwaveHelper = CreateObject<MmWaveHelper>();
  mmwaveHelper->SetPathlossModelType("ns3::ThreeGppUmiStreetCanyonPropagationLossModel");
  mmwaveHelper->SetChannelConditionModelType("ns3::ThreeGppUmiStreetCanyonChannelConditionModel");

  Ptr <MmWavePointToPointEpcHelper> epcHelper = CreateObject<MmWavePointToPointEpcHelper>();
  mmwaveHelper->SetEpcHelper(epcHelper);

  uint8_t nMmWaveEnbNodes = static_cast<uint8_t>(mmWaveEnbNodeCount);
  // GlobalValue::GetValueByName ("N_LteEnbNodes", uintegerValue);
  uint8_t nLteEnbNodes = 1; //uintegerValue.Get();
  uint32_t ues = ueCount;
  // TODO: discuss number of UEs implementation
  //uint8_t nUeNodes = ues * nMmWaveEnbNodes;
  uint8_t nUeNodes = ues;
  NS_LOG_INFO(" Bandwidth " << bandwidth << " centerFrequency " << double(centerFrequency)
                             << " isd_cell " << isd_cell << " numAntennasMcUe " << numAntennasMcUe
                             << " numAntennasMmWave " << numAntennasMmWave << " nMmWaveEnbNodes "
                             << unsigned(nMmWaveEnbNodes));

  // Get SGW/PGW and create a single RemoteHost
  Ptr <Node> pgw = epcHelper->GetPgwNode();
  NodeContainer remoteHostContainer;
  remoteHostContainer.Create(1);
  Ptr <Node> remoteHost = remoteHostContainer.Get(0);
  InternetStackHelper internet;
  internet.Install(remoteHostContainer);

  // Create the Internet by connecting remoteHost to pgw. Setup routing too
  PointToPointHelper p2ph;
  p2ph.SetDeviceAttribute("DataRate", DataRateValue(DataRate("100Gb/s")));
  p2ph.SetDeviceAttribute("Mtu", UintegerValue(2500));
  p2ph.SetChannelAttribute("Delay", TimeValue(Seconds(0.010)));
  NetDeviceContainer internetDevices = p2ph.Install(pgw, remoteHost);
  Ipv4AddressHelper ipv4h;
  ipv4h.SetBase("1.0.0.0", "255.0.0.0");
  Ipv4InterfaceContainer internetIpIfaces = ipv4h.Assign(internetDevices);
  // interface 0 is localhost, 1 is the p2p device
  Ipv4Address remoteHostAddr = internetIpIfaces.GetAddress(1);
  Ipv4StaticRoutingHelper ipv4RoutingHelper;
  Ptr <Ipv4StaticRouting> remoteHostStaticRouting =
      ipv4RoutingHelper.GetStaticRouting(remoteHost->GetObject<Ipv4>());
  remoteHostStaticRouting->AddNetworkRouteTo(Ipv4Address("7.0.0.0"), Ipv4Mask("255.0.0.0"), 1);

  // create LTE, mmWave eNB nodes and UE node
  NodeContainer ueNodes;
  NodeContainer mmWaveEnbNodes;
  NodeContainer lteEnbNodes;
  NodeContainer allEnbNodes;
  mmWaveEnbNodes.Create(nMmWaveEnbNodes);
  lteEnbNodes.Create(nLteEnbNodes);
  ueNodes.Create(nUeNodes);
  allEnbNodes.Add(lteEnbNodes);
  allEnbNodes.Add(mmWaveEnbNodes);

  NodeContainerManager::GetInstance().SetMmWaveEnbNodes(mmWaveEnbNodes);

  // Position
  Vector centerPosition = Vector(maxXAxis / 2, maxYAxis / 2, 3);

  // Install Mobility Model
  Ptr <ListPositionAllocator> enbPositionAlloc = CreateObject<ListPositionAllocator>();

  // We want a center with one LTE enb and one mmWave co-located in the same place
  enbPositionAlloc->Add(centerPosition);
  enbPositionAlloc->Add(centerPosition);
  double x, y;
  double nConstellation = nMmWaveEnbNodes - 1;

  // This guarantee that each of the rest BSs is placed at the same distance from the two co-located in the center
  for (int8_t i = 0; i < nConstellation; ++i) {
      x = isd_cell * cos((2 * M_PI * i) / (nConstellation));
      y = isd_cell * sin((2 * M_PI * i) / (nConstellation));
      enbPositionAlloc->Add(Vector(centerPosition.x + x, centerPosition.y + y, 3));
    }

  MobilityHelper enbmobility;
  enbmobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
  enbmobility.SetPositionAllocator(enbPositionAlloc);
  enbmobility.Install(allEnbNodes);

  // Cameras and virtual sensors are static network endpoints, while vehicle
  // UEs move through the cells to trigger real handovers without forcing every
  // UE into an expensive random-walk mobility model.
  uint32_t vehicleStartIndex = (vehicleUeCount >= ueNodes.GetN()) ? 0 : (ueNodes.GetN() - vehicleUeCount);
  uint32_t cameraStaticCount = std::min<uint32_t>(cameraUeCount, vehicleStartIndex);
  uint32_t sensorStaticCount = (vehicleStartIndex > cameraStaticCount) ? (vehicleStartIndex - cameraStaticCount) : 0;

  NodeContainer staticUeNodes;
  for (uint32_t u = 0; u < vehicleStartIndex; ++u)
    {
      staticUeNodes.Add(ueNodes.Get(u));
    }

  if (staticUeNodes.GetN() > 0)
    {
      MobilityHelper staticUeMobility;
      Ptr<ListPositionAllocator> staticUePositionAlloc = CreateObject<ListPositionAllocator>();

      for (uint32_t u = 0; u < vehicleStartIndex; ++u)
        {
          Vector position;
          if (u < cameraStaticCount)
            {
              double angle = (2.0 * M_PI * static_cast<double>(u)) /
                             std::max<uint32_t>(1, cameraStaticCount);
              double radius = 180.0 + 35.0 * static_cast<double>(u % 2);
              position = Vector(centerPosition.x + radius * std::cos(angle),
                                centerPosition.y + radius * std::sin(angle),
                                1.5);
            }
          else
            {
              uint32_t sensorIndex = u - cameraStaticCount;
              double angle = (2.0 * M_PI * static_cast<double>(sensorIndex)) /
                             std::max<uint32_t>(1, sensorStaticCount);
              double radius = 220.0 + 25.0 * static_cast<double>(sensorIndex % 3);
              position = Vector(centerPosition.x + radius * std::cos(angle + 0.35),
                                centerPosition.y + radius * std::sin(angle + 0.35),
                                1.5);
            }
          staticUePositionAlloc->Add(position);
        }

      staticUeMobility.SetMobilityModel("ns3::ConstantPositionMobilityModel");
      staticUeMobility.SetPositionAllocator(staticUePositionAlloc);
      staticUeMobility.Install(staticUeNodes);
    }

  if (vehicleStartIndex < ueNodes.GetN())
    {
      NodeContainer vehicleUeNodes;
      Ptr<ListPositionAllocator> vehiclePositionAlloc = CreateObject<ListPositionAllocator>();
      Ptr<UniformRandomVariable> vehicleSpeed = CreateObject<UniformRandomVariable>();
      vehicleSpeed->SetAttribute("Min", DoubleValue(ueSpeedMin));
      vehicleSpeed->SetAttribute("Max", DoubleValue(ueSpeedMax));

      double laneCenterY = centerPosition.y + 25.0;
      for (uint32_t u = vehicleStartIndex; u < ueNodes.GetN(); ++u)
        {
          vehicleUeNodes.Add(ueNodes.Get(u));
          double laneOffset = 18.0 * static_cast<double>(u - vehicleStartIndex);
          double startX = std::max(40.0, centerPosition.x - (1.4 * isd_cell) - laneOffset);
          double startY = laneCenterY + 12.0 * ((u - vehicleStartIndex) % 2 == 0 ? 1.0 : -1.0);
          vehiclePositionAlloc->Add(Vector(startX, startY, 1.5));
        }

      MobilityHelper vehicleMobility;
      vehicleMobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
      vehicleMobility.SetPositionAllocator(vehiclePositionAlloc);
      vehicleMobility.Install(vehicleUeNodes);

      for (uint32_t idx = 0; idx < vehicleUeNodes.GetN(); ++idx)
        {
          Ptr<ConstantVelocityMobilityModel> mobility =
              vehicleUeNodes.Get(idx)->GetObject<ConstantVelocityMobilityModel>();
          if (mobility)
            {
              mobility->SetVelocity(Vector(vehicleSpeed->GetValue(), 0.0, 0.0));
            }
        }
    }

  // Install mmWave, lte, mc Devices to the nodesnumAntennasMmWave
  // The anti-trap attach margin must apply to MOVING UEs only: static
  // cameras/sensors deliver fine at the plain outage threshold, while
  // vehicles anchored onto marginal cells ping-pong between peers.
  // Vehicle IMSIs follow device creation order (IMSI = node index + 1).
  if(vehicleStartIndex < ueNodes.GetN())
    {
      std::ostringstream vehicleImsiList;
      for(uint32_t u = vehicleStartIndex; u < ueNodes.GetN(); ++u)
        {
          if(u > vehicleStartIndex)
            {
              vehicleImsiList << ",";
            }
          vehicleImsiList << (u + 1);
        }
      Config::SetGlobal("attachMarginImsiList", StringValue(vehicleImsiList.str()));
      NS_LOG_UNCOND("[MOBILITY] attach margin applied to vehicle IMSIs: " << vehicleImsiList.str());
    }
  NetDeviceContainer lteEnbDevs = mmwaveHelper->InstallLteEnbDevice(lteEnbNodes);
  NetDeviceContainer mmWaveEnbDevs = mmwaveHelper->InstallEnbDevice(mmWaveEnbNodes);
  NetDeviceContainer ueAccessDevs = useMcUeDevices ? mmwaveHelper->InstallMcUeDevice(ueNodes)
                                                   : mmwaveHelper->InstallUeDevice(ueNodes);

  // Install the IP stack on the UEs
  internet.Install(ueNodes);
  Ipv4InterfaceContainer ueIpIface;

  ueIpIface = epcHelper->AssignUeIpv4Address(NetDeviceContainer(ueAccessDevs));
  // Assign IP address to UEs, and install applications
  for (uint32_t u = 0; u < ueNodes.GetN(); ++u) {
      Ptr <Node> ueNode = ueNodes.Get(u);
      // Set the default gateway for the UE
      Ptr <Ipv4StaticRouting> ueStaticRouting =
          ipv4RoutingHelper.GetStaticRouting(ueNode->GetObject<Ipv4>());
      ueStaticRouting->SetDefaultRoute(epcHelper->GetUeDefaultGatewayAddress(), 1);
    }

  // Add X2 interfaces
  mmwaveHelper->AddX2Interface(lteEnbNodes, mmWaveEnbNodes);

  if (enableTraces && !enableTracesAfterAttach) {
      EnableScenarioTraces (mmwaveHelper, nativeMinimalTraces, nativeAggregatedEvidence);
    }

 // for (uint16_t i = 0; i < mmWaveEnbNodes.GetN(); ++i)
//{
 // for (uint16_t j = i+1; j < mmWaveEnbNodes.GetN(); ++j) 
  //{
   // if (i != j)
    //{
     // mmwaveHelper->AddX2Interface(mmWaveEnbNodes.Get(i), mmWaveEnbNodes.Get(j));
    //}
  //}
//}

  // The EPC fork does not implement a post-attach dedicated-bearer
  // transaction.  For the protected vehicle profile, configure the five
  // canonical IMSIs to receive the V2X/URLLC QoS as their initial bearer.
  // This preserves the intended radio/QoS behavior without racing or
  // duplicating the default bearer setup.
  if (ranPressureProfile == "tasam_training_economic_vehicle_safe_v1" ||
      ranPressureProfile == "tasam_training_balanced_v4_v2x" ||
      ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr" ||
      ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr_priority" ||
      ranPressureProfile == "tasam_training_balanced_v5_v2x_gbr_deadline_nonmc" ||
      ranPressureProfile == "tasam_training_balanced_v5_v2x_gbr_deadline_mc" ||
      ranPressureProfile == "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback" ||
      ranPressureProfile == "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max")
    {
      mmwaveHelper->SetAttribute("VehicleBearerStartImsi", UintegerValue(16));
      mmwaveHelper->SetAttribute("VehicleBearerEndImsi", UintegerValue(20));

      // The GBR bearer must carry an actual rate reservation.  The
      // balanced curriculum reaches a vehicle multiplier of 4.0, so reserve
      // the maximum offered rate rather than the bootstrap rate.  This is
      // enabled only by the versioned _gbr profile; the historical v4_v2x
      // profile remains QCI-only and its evidence is immutable.
      if (ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr" ||
          ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr_priority" ||
          strictVehicleProfile)
        {
          const uint64_t baseRateBps =
              (static_cast<uint64_t>(vehiclePacketSizeBytes) * 8ULL * 1000000ULL) /
              std::max<uint32_t>(1, vehiclePacketIntervalUs);
          const uint64_t gbrDlBps = baseRateBps * 4ULL;
          const uint64_t mbrDlBps = (gbrDlBps * 5ULL) / 4ULL;
          mmwaveHelper->SetAttribute("VehicleBearerGbrDl", UintegerValue(gbrDlBps));
          mmwaveHelper->SetAttribute("VehicleBearerMbrDl", UintegerValue(mbrDlBps));
        }
    }

  // Manual attachment
  if (useMcUeDevices)
    {
      mmwaveHelper->AttachToClosestEnb(ueAccessDevs, mmWaveEnbDevs, lteEnbDevs);
    }
  else
    {
      mmwaveHelper->AttachToClosestEnb(ueAccessDevs, mmWaveEnbDevs);
    }

  for (uint32_t u = vehicleStartIndex; u < ueNodes.GetN(); ++u)
    {
      NS_LOG_UNCOND("[QOS] autonomous_vehicle_bearer=GBR_V2X_MESSAGES ue_index=" << u
                    << " canonical_imsi=" << (16 + (u - vehicleStartIndex))
                    << " activation=initial_epc_bearer");
    }

  // Record the QoS contract used by the versioned V2X profile.  The current
  // topology keeps the five canonical vehicles on cell 4; the association
  // trace remains the authoritative runtime evidence and this manifest is
  // only the immutable bearer configuration record.
  if (ranPressureProfile == "tasam_training_balanced_v4_v2x" ||
      ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr" ||
      ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr_priority" ||
      strictVehicleProfile)
    {
      StringValue vehicleEnergyOutputDirValue;
      GlobalValue::GetValueByName ("energyOutputDir", vehicleEnergyOutputDirValue);
      const std::string vehicleEnergyOutputDir = vehicleEnergyOutputDirValue.Get ();
      const std::string vehicleManifest = vehicleEnergyOutputDir.empty ()
                                              ? std::string ("VehicleBearerManifest.json")
                                              : vehicleEnergyOutputDir + "/VehicleBearerManifest.json";
      std::ofstream bearer (vehicleManifest, std::ios_base::out | std::ios_base::trunc);
      const uint64_t baseRateBps =
          (static_cast<uint64_t>(vehiclePacketSizeBytes) * 8ULL * 1000000ULL) /
          std::max<uint32_t>(1, vehiclePacketIntervalUs);
      const bool explicitGbr = ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr" ||
                               ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr_priority" ||
                               strictVehicleProfile;
      const uint64_t gbrDlBps = explicitGbr ? baseRateBps * 4ULL : 0ULL;
      const uint64_t mbrDlBps = explicitGbr ? (gbrDlBps * 5ULL) / 4ULL : 0ULL;
      bearer << "{\"schema\":\"greenran.ns3.vehicle_bearer_manifest.v1\"," 
             << "\"profile\":\"" << ranPressureProfile << "\"," 
             << "\"qci\":\"GBR_V2X_MESSAGES\",\"qci_value\":75,"
             << "\"priority\":25,\"cell_id\":4,"
             << "\"scheduler_gbr_priority\":"
             << (ranPressureProfile == "tasam_training_balanced_v4_v2x_gbr_priority" || strictVehicleProfile ? "true" : "false")
             << ","
             << "\"metric_contract\":\""
             << (strictVehicleProfile ? "per_pdu_cohort_v1" : "legacy_epoch_aggregate") << "\","
             << "\"connectivity_mode\":\""
             << ((ranPressureProfile == "tasam_training_balanced_v5_v2x_gbr_deadline_mc" ||
                  ranPressureProfile == "tasam_training_balanced_v6_v2x_gbr_deadline_mc_fallback" ||
                  ranPressureProfile == "tasam_training_balanced_v6_1_v2x_gbr_deadline_mc_fallback_baseline_max")
                     ? "lte_anchored_mc" : "mmwave_only") << "\","
             << "\"scheduler_policy\":\""
             << (strictVehicleProfile ? "gbr_debt_rr_v1" : "legacy") << "\","
             << "\"loss_grace_ms\":" << (strictVehicleProfile ? 1000 : 0) << ","
             << "\"gbr_dl_bps\":" << gbrDlBps << ",\"mbr_dl_bps\":" << mbrDlBps << ","
             << "\"packet_interval_us\":"
             << (std::getenv ("GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US") == nullptr
                     ? "null"
                     : std::getenv ("GREENRAN_NS3_VEHICLE_PACKET_INTERVAL_US"))
             << ",\"vehicles\":[";
      for (uint32_t index = 16; index <= 20; ++index)
        {
          if (index != 16)
            bearer << ",";
          bearer << "{\"imsi\":" << index << ",\"cell_id\":4"
                 << ",\"qci\":\"GBR_V2X_MESSAGES\",\"priority\":25}";
        }
      bearer << "]}\n";
    }

  if (enableTraces && enableTracesAfterAttach)
    {
      EnableScenarioTraces (mmwaveHelper, nativeMinimalTraces, nativeAggregatedEvidence);
    }

  BasicEnergySourceHelper basicEnergySourceHelper;
  basicEnergySourceHelper.Set ("BasicEnergySourceInitialEnergyJ", DoubleValue (1000000000000));
  basicEnergySourceHelper.Set ("BasicEnergySupplyVoltageV", DoubleValue (5.0));
  energy::EnergySourceContainer sources = basicEnergySourceHelper.Install (mmWaveEnbNodes);
  MmWaveRadioEnergyModelEnbHelper nrEnbHelper;

  energy::DeviceEnergyModelContainer deviceEModel = nrEnbHelper.Install (mmWaveEnbDevs, sources);

  UintegerValue fixedTxPowerPercentValue;
  GlobalValue::GetValueByName ("fixedTxPowerPercent", fixedTxPowerPercentValue);
  const uint32_t fixedTxPowerPercent = fixedTxPowerPercentValue.Get ();
  UintegerValue activeCellsValue;
  GlobalValue::GetValueByName ("activeCells", activeCellsValue);
  if (fixedTxPowerPercent != 100 || activeCellsValue.Get () != mmWaveEnbDevs.GetN ())
    {
      Simulator::Schedule (MilliSeconds (1), &ApplyFixedTasamPower, mmWaveEnbDevs,
                           fixedTxPowerPercent, activeCellsValue.Get ());
    }

  GlobalValue::GetValueByName ("simTime", doubleValue);
  double simTime = doubleValue.Get ();
  StringValue energyOutputDirValue;
  GlobalValue::GetValueByName ("energyOutputDir", energyOutputDirValue);
  const std::string energyOutputDir = energyOutputDirValue.Get ();
  // A 100 ms safety-channel trace makes E2 acceptance independently
  // observable at scheduler/PHY level without inflating analytical logs.
  const std::string tasamControlTrace = energyOutputDir.empty ()
                                            ? std::string ("TasamControlObservations.csv")
                                            : energyOutputDir + "/TasamControlObservations.csv";
  {
    std::ofstream trace (tasamControlTrace, std::ios_base::out | std::ios_base::trunc);
    trace << "Time,CellId,SchedulerTransactionId,PowerTransactionId,ActiveUes,"
             "TxPowerPercent,TxPowerDbm,NominalTxPowerDbm,ObservationKind,"
             "PolicyActive,PolicyExpiryTime,SourceGeneration,AssociationEpoch,"
             "ActiveDlSymbols,ActiveDlSymbolCapacity,"
             "RequestedDiscretionaryDlSymbolsBp,AppliedDiscretionaryDlSymbolsBp,"
             "MandatoryDlSymbols,DiscretionaryDlSymbols,WithheldDlSymbols,SleepTransactionId,"
             "CampaignId,EvidenceVersion,"
             "CampaignGeneration,DecisionId,ActionCorrelationId,NativeControlSequence,"
             "NativeAllocationSource,PowerLeaseFresh"
          << std::endl;
  }
  const std::string e2NodeManifest = energyOutputDir.empty ()
                                         ? std::string ("E2NodeManifest.json")
                                         : energyOutputDir + "/E2NodeManifest.json";
  {
    std::ofstream manifest (e2NodeManifest, std::ios_base::out | std::ios_base::trunc);
    manifest << "{\"schema\":\"greenran.ns3.e2_node_manifest.v1\"," 
             << "\"campaign_id\":\"" << (std::getenv ("GREENRAN_CAMPAIGN_ID") == nullptr
                                                ? ""
                                                : std::getenv ("GREENRAN_CAMPAIGN_ID"))
             << "\",\"evidence_version\":\""
             << NativeEvidenceVersion ()
             << "\",\"source_generation\":\""
             << (std::getenv ("GREENRAN_NATIVE_SOURCE_GENERATION") == nullptr
                     ? NativeSourceGeneration ()
                     : std::getenv ("GREENRAN_NATIVE_SOURCE_GENERATION"))
             << "\",\"nodes\":[";
    for (uint32_t index = 0; index < mmWaveEnbDevs.GetN (); ++index)
      {
        Ptr<MmWaveEnbNetDevice> enb = DynamicCast<MmWaveEnbNetDevice> (mmWaveEnbDevs.Get (index));
        if (index != 0)
          {
            manifest << ",";
          }
        manifest << "{\"cell_id\":" << enb->GetCellId ()
                 << ",\"ns3_node_id\":" << enb->GetNode ()->GetId ()
                 // The current FlexRIC adapter uses the ns-3 node identity
                 // as its E2 endpoint identity.  Emit both names explicitly
                 // so the Python actuator never has to infer the mapping by
                 // array position.
                 << ",\"e2_node_id\":" << enb->GetNode ()->GetId ()
                 << ",\"control_protocol\":\"e2_rc\""
                 << ",\"rc_control_supported\":true}";
      }
    manifest << "]}\n";
  }
  const std::string tasamAssociationTrace = energyOutputDir.empty ()
                                               ? std::string ("TasamAssociationTrace.csv")
                                               : energyOutputDir + "/TasamAssociationTrace.csv";
  {
    std::ofstream association (tasamAssociationTrace,
                               std::ios_base::out | std::ios_base::trunc);
    association << "Time,CellId,Rnti,Imsi,AssociationEpoch,EvidenceVersion,CampaignId,SourceGeneration,"
                   "TransactionId,NativeControlSequence,DecisionId,ActionCorrelationId,ObservationKind,"
                   "AttachedUeCount,SleepTransactionId"
                << std::endl;
  }
  const double nativeEvidencePeriodSeconds = nativeAggregatedEvidence
                                                 ? static_cast<double> (nativeEvidencePeriodMs) / 1000.0
                                                 : 0.1;
  Simulator::Schedule (MilliSeconds (nativeAggregatedEvidence ? nativeEvidencePeriodMs : 100),
                       &TasamControlSnapshot, mmWaveEnbDevs, tasamControlTrace,
                       nativeEvidencePeriodSeconds, simTime);
  Simulator::Schedule (MilliSeconds (nativeAggregatedEvidence ? nativeEvidencePeriodMs : 100),
                       &TasamAssociationSnapshot, mmWaveEnbDevs, tasamAssociationTrace,
                       nativeEvidencePeriodSeconds, simTime);
  if (strictVehicleProfile)
    {
      const std::string vehicleSchedulerTrace = energyOutputDir.empty ()
                                                    ? std::string ("VehicleSchedulerTrace.csv")
                                                    : energyOutputDir + "/VehicleSchedulerTrace.csv";
      {
        std::ofstream schedulerTrace (vehicleSchedulerTrace,
                                     std::ios_base::out | std::ios_base::trunc);
        schedulerTrace << "Time,CellId,Rnti,Imsi,Cqi,Mcs,RlcQueueBytes,GbrDlBps,"
                          "GbrCreditBytes,RequestedSymbols,GrantedSymbols,GrantedTbBytes,"
                          "HarqNacks,HarqMaxRetxDrops,HarqRetxSymbols,DeficitReason"
                       << std::endl;
      }
      Simulator::Schedule (MilliSeconds (nativeAggregatedEvidence ? nativeEvidencePeriodMs : 100),
                           &VehicleSchedulerSnapshot, mmWaveEnbDevs, vehicleSchedulerTrace,
                           nativeEvidencePeriodSeconds, simTime,
                           strictVehicleProfile
                               ? ((static_cast<uint64_t> (vehiclePacketSizeBytes) * 8ULL * 1000000ULL) /
                                  std::max<uint32_t> (1, vehiclePacketIntervalUs)) * 4ULL
                               : 0ULL);
    }
  int numPrints = simTime / nativeEvidencePeriodSeconds;
  bool enableVerboseRuntimeLogging = false;
  bool enablePositionCsvDump = false;
  bool enableEnergyCsvDump = false;
  BooleanValue enableVerboseRuntimeLoggingValue;
  BooleanValue enablePositionCsvDumpValue;
  BooleanValue enableEnergyCsvDumpValue;
  GlobalValue::GetValueByName ("enableVerboseRuntimeLogging", enableVerboseRuntimeLoggingValue);
  GlobalValue::GetValueByName ("enablePositionCsvDump", enablePositionCsvDumpValue);
  GlobalValue::GetValueByName ("enableEnergyCsvDump", enableEnergyCsvDumpValue);
  enableVerboseRuntimeLogging = enableVerboseRuntimeLoggingValue.Get ();
  enablePositionCsvDump = enablePositionCsvDumpValue.Get ();
  enableEnergyCsvDump = enableEnergyCsvDumpValue.Get ();

  if (enableEnergyCsvDump || enableVerboseRuntimeLogging)
    {
      std::vector<std::ofstream> outFiles;
      if (enableEnergyCsvDump)
        {
          for (int x = 0; x < nMmWaveEnbNodes; ++x)
            {
              std::ostringstream energyFileName;
              energyFileName << (energyOutputDir.empty () ? std::string (".") : energyOutputDir)
                             << "/energyfilecell" << x + 2 << ".csv";

              std::ofstream outFile;
              outFile.open (energyFileName.str (), std::ios_base::out | std::ios_base::trunc);
              outFile << "Time,NetEnergy,DiffEnergy,IdleSeconds,TxSeconds,DataSeconds,CtrlSeconds,TxPowerPercent,ActiveCell,PowerTransactionId,ModelTxPowerPercent,TasamTxPowerPercent,PowerLeaseFresh" << std::endl;

              outFiles.push_back (std::move (outFile));
            }
        }

      for (int x = 0; x < nMmWaveEnbNodes; ++x)
        {
          std::ostringstream filename;
          filename << (energyOutputDir.empty () ? std::string (".") : energyOutputDir)
                   << "/energyfilecell" << x + 2 << ".csv";
          // Keep a bounded, comparable corpus: one cumulative sample every
          // simTime/12 plus a final sample strictly before
          // Simulator::Stop(simTime) (ns-3 does not guarantee that an event
          // scheduled at the exact stop timestamp will run first).  The
          // final row is what lets the validator integrate the whole
          // post-warmup window instead of stopping one interval early.
          constexpr uint32_t energySampleCount = 12;
          for (uint32_t sample = 1; sample <= energySampleCount; ++sample)
            {
              const double sampleTime = sample < energySampleCount
                  ? simTime * static_cast<double> (sample) / energySampleCount
                  : std::max (0.0, simTime - 0.1);
              Simulator::Schedule (
                  Seconds (sampleTime),
                  &EnergyConsumptionSnapshot,
                  x,
                  filename.str (),
                  deviceEModel.Get (x),
                  DynamicCast<MmWaveEnbNetDevice> (mmWaveEnbDevs.Get (x)));
            }
          if (enableVerboseRuntimeLogging)
            {
              for (int i = 0; i < numPrints; i++)
                {
                  Simulator::Schedule (Seconds (i * simTime / numPrints), &EnergyConsumptionPrint, x);
                }
            }
        }
    }

  // Install and start applications
  // On the remoteHost there is UDP OnOff Application

  uint16_t portUdp = 60000;
  Address sinkLocalAddressUdp(InetSocketAddress(Ipv4Address::GetAny(), portUdp));
  PacketSinkHelper sinkHelperUdp("ns3::UdpSocketFactory", sinkLocalAddressUdp);
  AddressValue serverAddressUdp(InetSocketAddress(remoteHostAddr, portUdp));

  ApplicationContainer sinkApp;
  sinkApp.Add(sinkHelperUdp.Install(remoteHost));

  ApplicationContainer clientApp;
  std::vector<Ptr<UdpClient>> cameraClients;
  std::vector<Ptr<UdpClient>> backgroundClients;
  std::vector<Ptr<UdpClient>> vehicleClients;
  const uint32_t cameraBasePacketSizeBytes = cameraPacketSizeBytes;
  const uint32_t cameraBaseIntervalUs = cameraPacketIntervalUs;
  const uint32_t backgroundBasePacketSizeBytes = backgroundPacketSizeBytes;
  const uint32_t backgroundBaseIntervalUs = backgroundPacketIntervalUs;
  const uint32_t vehicleBasePacketSizeBytes = vehiclePacketSizeBytes;
  const uint32_t vehicleBaseIntervalUs = vehiclePacketIntervalUs;

  for (uint32_t u = 0; u < ueNodes.GetN(); ++u) {
      // Full traffic
      PacketSinkHelper dlPacketSinkHelper("ns3::UdpSocketFactory",
                                           InetSocketAddress(Ipv4Address::GetAny(), 1234));
      sinkApp.Add(dlPacketSinkHelper.Install(ueNodes.Get(u)));
      UdpClientHelper dlClient(ueIpIface.GetAddress(u), 1234);
      dlClient.SetAttribute("MaxPackets", UintegerValue(UINT32_MAX));
      if (u < std::min<uint32_t>(cameraUeCount, ueNodes.GetN()))
        {
          dlClient.SetAttribute("Interval", TimeValue(MicroSeconds(cameraBaseIntervalUs)));
          dlClient.SetAttribute("PacketSize", UintegerValue(cameraBasePacketSizeBytes));
        }
      else if (u >= vehicleStartIndex)
        {
          dlClient.SetAttribute("Interval", TimeValue(MicroSeconds(vehicleBaseIntervalUs)));
          dlClient.SetAttribute("PacketSize", UintegerValue(vehicleBasePacketSizeBytes));
        }
      else
        {
          dlClient.SetAttribute("Interval", TimeValue(MicroSeconds(backgroundBaseIntervalUs)));
          dlClient.SetAttribute("PacketSize", UintegerValue(backgroundBasePacketSizeBytes));
        }
      ApplicationContainer installedClient = dlClient.Install(remoteHost);
      clientApp.Add(installedClient);
      Ptr<UdpClient> udpClient = DynamicCast<UdpClient>(installedClient.Get(0));
      if (udpClient)
        {
          if (u < std::min<uint32_t>(cameraUeCount, ueNodes.GetN()))
            {
              cameraClients.push_back(udpClient);
            }
          else if (u >= vehicleStartIndex)
            {
              vehicleClients.push_back(udpClient);
            }
          else
            {
              backgroundClients.push_back(udpClient);
            }
        }
    }

  ScheduleRanPressureProfile(
      BuildRanPressureStages(ranPressureProfile),
      cameraClients,
      backgroundClients,
      vehicleClients,
      cameraBasePacketSizeBytes,
      cameraBaseIntervalUs,
      backgroundBasePacketSizeBytes,
      backgroundBaseIntervalUs,
      vehicleBasePacketSizeBytes,
      vehicleBaseIntervalUs,
      simTime);

  // Start applications

  sinkApp.Start (Seconds (0));

  clientApp.Start(MilliSeconds(100));
  clientApp.Stop(Seconds(simTime - 0.1));

  struct timeval time_now{};
  gettimeofday(&time_now, nullptr);
  uint64_t t_startTime_simid = (time_now.tv_sec * 1000) + (time_now.tv_usec / 1000);
  if (enablePositionCsvDump)
    {
      std::string ue_poss_out = "ue_position.txt";
      ClearFile(ue_poss_out, t_startTime_simid);
      ClearFile("enbs.txt", t_startTime_simid);
      ClearFile("gnbs.txt", t_startTime_simid);
      // Since nodes are randomly allocated during each run we always need to print their positions
      PrintGnuplottableUeListToFile("ues.txt");

      int nodecount = int(NodeList::GetNNodes());
      int UE_iterator = nodecount - int (nUeNodes);

      for (int i = 1; i < numPrints; i++) {
          Simulator::Schedule(Seconds(i * simTime / numPrints), &PrintGnuplottableEnbListToFile, t_startTime_simid);
          for (uint32_t j = 0; j < ueNodes.GetN(); j++) {
              Simulator::Schedule(Seconds(i * simTime / numPrints), &PrintPosition, ueNodes.Get(j),
                                   j + UE_iterator, ue_poss_out, t_startTime_simid);
            }
        }
    }

  // Since nodes are randomly allocated during each run we always need to print their positions
  //PrintGnuplottableUeListToFile ("ues.txt");
  // PrintGnuplottableEnbListToFile ("enbs.txt");

  bool run = true;
  if (run) {
      NS_LOG_UNCOND("Simulation time is " << simTime << " seconds ");
      Simulator::Stop(Seconds(simTime));
      NS_LOG_INFO("Run Simulation.");
      Simulator::Run();
    }

  Simulator::Destroy();
  NS_LOG_INFO("Done.");
  return 0;
}
