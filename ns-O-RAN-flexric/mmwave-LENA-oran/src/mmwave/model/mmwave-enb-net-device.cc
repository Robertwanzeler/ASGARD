/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
*   Copyright (c) 2011 Centre Tecnologic de Telecomunicacions de Catalunya (CTTC)
*   Copyright (c) 2015, NYU WIRELESS, Tandon School of Engineering, New York University
*   Copyright (c) 2016, 2018, University of Padova, Dep. of Information Engineering, SIGNET lab.
*   Copyright (c) 2024 Orange Innovation Poland
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
 *                        Kamil Kociszewski <kamil.kociszewski@orange.com>
*
*       Modified by: Tommaso Zugno <tommasozugno@gmail.com>
*                                Integration of Carrier Aggregation
*/

#include "ns3/mmwave-helper.h"
#include <ns3/llc-snap-header.h>
#include <ns3/simulator.h>
#include <ns3/callback.h>
#include <ns3/node.h>
#include <ns3/packet.h>
#include <ns3/lte-enb-rrc.h>
#include "mmwave-net-device.h"
#include "mmwave-radio-energy-model-enb.h"
#include <iostream>
#include <set>
#include <ns3/packet-burst.h>
#include <ns3/uinteger.h>
#include <ns3/trace-source-accessor.h>
#include <ns3/pointer.h>
#include <ns3/enum.h>
#include <ns3/uinteger.h>
#include <ns3/double.h>
#include "mmwave-enb-net-device.h"
#include "mmwave-ue-net-device.h"
#include <ns3/lte-enb-rrc.h>
#include <ns3/ipv4-l3-protocol.h>
#include <ns3/ipv6-l3-protocol.h>
#include <ns3/abort.h>
#include <ns3/log.h>
#include <ns3/lte-enb-component-carrier-manager.h>
#include <ns3/mmwave-component-carrier-enb.h>
#include <ns3/mmwave-flex-tti-mac-scheduler.h>
#include <ns3/mmwave-radio-energy-model-enb.h>
#include <ns3/config.h>
#include <ns3/lte-rlc-um.h>
#include <ns3/lte-rlc-um-lowlat.h>
#include <ns3/lte-rlc-am.h>
#include "UEID-GNB.h"
#include "E2SM-RC-ControlMessage-Format1-Item.h"
#include "RANParameter-ValueType-Choice-ElementFalse.h"
#include <ns3/mmwave-indication-message-helper.h>
#include "node-container-manager.h"
#include <string.h>
#include <arpa/inet.h>
#include "encode_e2apv1.hpp"
#include "ns3/network-module.h"
#include <any>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>
#include <vector>

namespace ns3 {

NS_LOG_COMPONENT_DEFINE ("MmWaveEnbNetDevice");

namespace mmwave {

NS_OBJECT_ENSURE_REGISTERED (MmWaveEnbNetDevice);


bool lessThan(int x, int y) {
  return x < y;
}

bool greaterThan(int x, int y) {
  return x > y;
}

bool equal(int x, int y) {
  return x == y;
}

std::vector<std::function<bool(int, int)>> MATH_CALL_BACKS = {
  equal, greaterThan, lessThan
};

/**
* KPM Subscription Request callback.
* This function is triggered whenever a RIC Subscription Request for
* the KPM RAN Function is received.
*
* \param pdu request message
*/


// Function to Calculate the average of PRBs
double
MmWaveEnbNetDevice::CalculatePrbAverage() 
{
  double totalPrbUtilization = 0;
  auto ueMap = m_rrc->GetUeMap();

  for (auto ue : ueMap)
  {
    uint16_t rnti = ue.second->GetRnti();
    double macNumberOfSymbols = m_e2DuCalculator->GetMacNumberOfSymbolsUeSpecific(rnti, m_cellId);
    
    auto phyMac = GetMac()->GetConfigurationParameters();
    Time reportingWindow = Simulator::Now() - m_e2DuCalculator->GetLastResetTime(rnti, m_cellId);
    double denominatorPrb = std::ceil(reportingWindow.GetNanoSeconds() / 
                           phyMac->GetSlotPeriod().GetNanoSeconds()) * 14;

    if (denominatorPrb > 0)
    {
      totalPrbUtilization += (macNumberOfSymbols / denominatorPrb) * 139;
    }
  }

  long dlAvailablePrbs = 139;
  double currentPrbValue = std::min((double)(totalPrbUtilization / dlAvailablePrbs * 100), 100.0);

  // Store current value
  m_prbHistory.push_back(currentPrbValue);
  
  NS_LOG_DEBUG("Current PRB Value: " << currentPrbValue << 
               " History Size: " << m_prbHistory.size() << "/" << MAX_PRB_HISTORY);

  // Only return average when we have exactly MAX_PRB_HISTORY points
  if (m_prbHistory.size() == MAX_PRB_HISTORY)
  {
    double sum = 0;
    for (auto prb : m_prbHistory)
    {
      sum += prb;
    }
    double average = sum / MAX_PRB_HISTORY;
    
    // Remove oldest value to maintain window
    m_prbHistory.erase(m_prbHistory.begin());
    
    NS_LOG_DEBUG("Returning PRB Average: " << average);
    return average;
  }
  
  // Return -1 to indicate not enough points yet
  NS_LOG_DEBUG("Not enough points yet, returning -1");
  return -1;
}

void
MmWaveEnbNetDevice::CheckReportingFlag()
{
  NS_LOG_FUNCTION(this);
  if (!m_stopSendingMessages && m_hasValidSubscription)
  {
    const auto &sub_map = m_e2term->SubscriptionMapRef();
    if (!sub_map.empty())
    {
      try 
      {
        const auto& expr = sub_map.at("Test Condition Expression");
        const auto& value = sub_map.at("Test Condition Value");
        
        int index = std::any_cast<int>(expr);
        int threshold = std::any_cast<int>(value);

        // Get current PRB average
        double currentPrbAvg = CalculatePrbAverage();
        
        // Only check conditions if we have enough points
        if (currentPrbAvg >= 0)
        {
          bool shouldReport = MATH_CALL_BACKS[index](currentPrbAvg, threshold);

          NS_LOG_DEBUG("Current PRB Average: " << currentPrbAvg << 
                       " Threshold: " << threshold << 
                       " Should Report: " << m_is_reported);
          // If we haven't started reporting yet, check if we should start
          if (!m_isReportingEnabled)
          {
            if (shouldReport)
            {
              m_is_reported = true;
              m_isReportingEnabled = true;
              BuildAndSendReportMessage(m_lastSubscriptionParams);
            }
          }
          else
          {
            // If reporting is already enabled, keep sending reports
           // BuildAndSendReportMessage(m_lastSubscriptionParams);
           m_is_reported = true;
           m_isReportingEnabled = true;

          }
        }
      }
      catch (const std::exception& e)
      {
        NS_LOG_ERROR("Error checking PRB usage: " << e.what());
      }
    }
    // Schedule next check
    Simulator::ScheduleWithContext(1, m_checkPeriod,
        &MmWaveEnbNetDevice::CheckReportingFlag, this);
  }
}


void
MmWaveEnbNetDevice::KpmSubscriptionCallback(E2AP_PDU_t *sub_req_pdu)
{
  NS_LOG_DEBUG("\nReceived RIC Subscription Request, cellId= " << m_cellId << "\n");

  // Store subscription parameters
  m_lastSubscriptionParams = m_e2term->ProcessRicSubscriptionRequest(sub_req_pdu);
  m_hasValidSubscription = true;

  NS_LOG_DEBUG("requestorId " << +m_lastSubscriptionParams.requestorId << 
               ", instanceId " << +m_lastSubscriptionParams.instanceId <<
               ", ranFuncionId " << +m_lastSubscriptionParams.ranFuncionId <<
               ", actionId " << +m_lastSubscriptionParams.actionId);

  const auto &sub_map = m_e2term->SubscriptionMapRef();
  if (!sub_map.empty())
  {
    try 
    {
      // Check if keys exist
      if (sub_map.find("Test Condition Expression") == sub_map.end() ||
          sub_map.find("Action Definition Format") == sub_map.end() ||
          sub_map.find("Test Condition Value") == sub_map.end())
      {
        NS_LOG_ERROR("Required keys not found in sub_map");
        return;
      }

      const auto& expr = sub_map.at("Test Condition Expression");
      const auto& action = sub_map.at("Action Definition Format");

      int index = std::any_cast<int>(expr);
      int action_def = std::any_cast<int>(action);

      if (index < 0 || index >= static_cast<int>(MATH_CALL_BACKS.size()))
      {
        NS_LOG_ERROR("Invalid index: " << index);
        return;
      }

      switch (action_def)
      {
        case E2SM_KPM_ActionDefinition__actionDefinition_formats_PR_actionDefinition_Format4:
          {
            // Clear PRB history at subscription start
            m_prbHistory.clear();
            
            // Start periodic PRB checking
            if (!m_stopSendingMessages)
            {
              Simulator::ScheduleWithContext(1, m_checkPeriod,
                  &MmWaveEnbNetDevice::CheckReportingFlag, this);
              
              NS_LOG_DEBUG("Started PRB monitoring with period " << 
                          m_checkPeriod.GetMilliSeconds() << "ms");
            }
          }
          break;

        default:
          NS_LOG_ERROR("Action Definition NOT supported");
          break;
      }
    }
    catch (const std::exception& e)
    {
      NS_LOG_ERROR("Error in KpmSubscriptionCallback: " << e.what());
    }
  }
}

void
MmWaveEnbNetDevice::stopSendingAndCancelSchedule ()
{
  m_stopSendingMessages = true;
}

TypeId
MmWaveEnbNetDevice::GetTypeId ()
{
  static TypeId tid =
      TypeId ("ns3::MmWaveEnbNetDevice")
          .SetParent<MmWaveNetDevice> ()
          .AddConstructor<MmWaveEnbNetDevice> ()
          .AddAttribute ("LteEnbComponentCarrierManager",
                         "The ComponentCarrierManager associated to this EnbNetDevice",
                         PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::m_componentCarrierManager),
                         MakePointerChecker<LteEnbComponentCarrierManager> ())
          .AddAttribute ("LteEnbRrc", "The RRC layer associated with the ENB", PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::m_rrc),
                         MakePointerChecker<LteEnbRrc> ())
          .AddAttribute ("E2Termination", "The E2 termination object associated to this node",
                         PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::SetE2Termination,
                                              &MmWaveEnbNetDevice::GetE2Termination),
                         MakePointerChecker<E2Termination> ())
          .AddAttribute ("CellId", "Cell Identifier", UintegerValue (0),
                         MakeUintegerAccessor (&MmWaveEnbNetDevice::m_cellId),
                         MakeUintegerChecker<uint16_t> ())
          .AddAttribute (
              "BasicCellId", "Basic cell ID. This is needed to properly loop over neighbors.",
              UintegerValue (1), MakeUintegerAccessor (&MmWaveEnbNetDevice::m_basicCellId),
              MakeUintegerChecker<uint16_t> ())
          .AddAttribute ("E2PdcpCalculator", "The PDCP calculator object for E2 reporting",
                         PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::m_e2PdcpStatsCalculator),
                         MakePointerChecker<MmWaveBearerStatsCalculator> ())
          .AddAttribute ("E2Periodicity", "Periodicity of E2 reporting (value in seconds)",
                         DoubleValue (0.1),
                         MakeDoubleAccessor (&MmWaveEnbNetDevice::m_e2Periodicity),
                         MakeDoubleChecker<double> ())
          .AddAttribute ("E2RlcCalculator", "The RLC calculator object for E2 reporting",
                         PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::m_e2RlcStatsCalculator),
                         MakePointerChecker<MmWaveBearerStatsCalculator> ())
          .AddAttribute ("E2DuCalculator", "The DU calculator object for E2 reporting",
                         PointerValue (),
                         MakePointerAccessor (&MmWaveEnbNetDevice::m_e2DuCalculator),
                         MakePointerChecker<MmWavePhyTrace> ())
          .AddAttribute ("EnableCuUpReport", "If true, send CuUpReport", BooleanValue (false),
                         MakeBooleanAccessor (&MmWaveEnbNetDevice::m_sendCuUp),
                         MakeBooleanChecker ())
          .AddAttribute ("EnableCuCpReport", "If true, send CuCpReport", BooleanValue (false),
                         MakeBooleanAccessor (&MmWaveEnbNetDevice::m_sendCuCp),
                         MakeBooleanChecker ())
          .AddAttribute ("EnableDuReport", "If true, send DuReport", BooleanValue (true),
                         MakeBooleanAccessor (&MmWaveEnbNetDevice::m_sendDu), MakeBooleanChecker ())
          .AddAttribute (
              "ReducedPmValues", "If true, send only a subset of pmValues", BooleanValue (false),
              MakeBooleanAccessor (&MmWaveEnbNetDevice::m_reducedPmValues), MakeBooleanChecker ())
          .AddAttribute ("EnableE2FileLogging",
                         "If true, force E2 indication generation and write E2 fields in csv file",
                         BooleanValue (false),
                         MakeBooleanAccessor (&MmWaveEnbNetDevice::m_forceE2FileLogging),
                         MakeBooleanChecker ())
          .AddAttribute ("KPM_E2functionID", "Function ID to subscribe", DoubleValue (2),
                         MakeDoubleAccessor (&MmWaveEnbNetDevice::e2_func_id),
                         MakeDoubleChecker<double> ())
          .AddAttribute ("RC_E2functionID", "Function ID to subscribe", DoubleValue (3),
                         MakeDoubleAccessor (&MmWaveEnbNetDevice::rc_e2_func_id),
                         MakeDoubleChecker<double> ());
  return tid;
}

MmWaveEnbNetDevice::MmWaveEnbNetDevice ()
    //:m_cellId(0),
    // m_Bandwidth (72),
    // m_Earfcn(1),
    : m_stopSendingMessages (false),
      m_componentCarrierManager (0),
      m_isConfigured (false),
      m_isReportingEnabled (false),
      m_reducedPmValues (false),
      m_forceE2FileLogging (false),
      m_cuUpFileName (),
      m_cuCpFileName (),
      m_duFileName (),
      m_prbHistory(),
      m_checkPeriod(MilliSeconds(100)),
      m_hasValidSubscription(false)


{
  NS_LOG_FUNCTION (this);
}

MmWaveEnbNetDevice::~MmWaveEnbNetDevice ()
{
  NS_LOG_FUNCTION (this);
}

// MmWaveEnbNetDevice::MmWaveEnbNetDevice(Ptr<E2Termination> e2Termination)
// {
//     m_e2term = e2Termination;
// }

void
MmWaveEnbNetDevice::DoInitialize (void)
{
  NS_LOG_FUNCTION (this);
  m_isConstructed = true;
  UpdateConfig ();
  for (auto it = m_ccMap.begin (); it != m_ccMap.end (); ++it)
    {
      it->second->Initialize ();
    }
  m_rrc->Initialize ();
  m_componentCarrierManager->Initialize ();

  if (m_sendCuCp == true)
    {
      // connect to callback
      Config::ConnectFailSafe (
          "/NodeList/*/DeviceList/*/LteEnbRrc/NotifyMmWaveSinr",
          MakeBoundCallback (&MmWaveEnbNetDevice::RegisterNewSinrReadingCallback, this));
    }
}

void
MmWaveEnbNetDevice::DoDispose ()
{
  NS_LOG_FUNCTION (this);

  m_rrc->Dispose ();
  m_rrc = 0;

  m_componentCarrierManager->Dispose ();
  m_componentCarrierManager = 0;
  // MmWaveComponentCarrierEnb::DoDispose() will call DoDispose
  // of its PHY, MAC, FFR and scheduler instance
  for (uint32_t i = 0; i < m_ccMap.size (); i++)
    {
      m_ccMap.at (i)->Dispose ();
      m_ccMap.at (i) = 0;
    }

  MmWaveNetDevice::DoDispose ();
}

void
MmWaveEnbNetDevice::RegisterNewSinrReadingCallback (Ptr<MmWaveEnbNetDevice> netDev,
                                                    std::string context, uint64_t imsi,
                                                    uint16_t cellId, long double sinr)
{
  netDev->RegisterNewSinrReading (imsi, cellId, sinr);
}

void
MmWaveEnbNetDevice::RegisterNewSinrReading (uint64_t imsi, uint16_t cellId, long double sinr)
{
  // check if the imsi is connected to this DU
  auto ueMap = m_rrc->GetUeMap ();
  bool imsiFound = false;

  for (auto ue : ueMap)
    {
      if (ue.second->GetImsi () == imsi)
        {
          imsiFound = true;
          break;
        }
    }

  if (imsiFound)
    {
      // we only need to save the last value, so we erase if exists already a value nd save the new one
      m_l3sinrMap[imsi][cellId] = sinr;
      NS_LOG_LOGIC (Simulator::Now ().GetSeconds ()
                    << " enbdev " << m_cellId << " UE " << imsi << " report for " << cellId
                    << " SINR " << m_l3sinrMap[imsi][cellId]);
    }
}

Ptr<MmWaveEnbPhy>
MmWaveEnbNetDevice::GetPhy (void) const
{
  NS_LOG_FUNCTION (this);
  auto carrier = m_ccMap.find (0);
  if (carrier == m_ccMap.end () || carrier->second == nullptr)
    {
      NS_LOG_UNCOND ("MmWaveEnbNetDevice: PHY requested before component carrier 0 is ready");
      return nullptr;
    }
  Ptr<MmWaveComponentCarrierEnb> enbCarrier = DynamicCast<MmWaveComponentCarrierEnb> (carrier->second);
  if (enbCarrier == nullptr)
    {
      NS_LOG_UNCOND ("MmWaveEnbNetDevice: component carrier 0 has unexpected type");
      return nullptr;
    }
  return enbCarrier->GetPhy ();
}

Ptr<MmWaveEnbPhy>
MmWaveEnbNetDevice::GetPhy (uint8_t index)
{
  auto carrier = m_ccMap.find (index);
  if (carrier == m_ccMap.end () || carrier->second == nullptr)
    {
      NS_LOG_UNCOND ("MmWaveEnbNetDevice: PHY requested before component carrier "
                     << static_cast<uint32_t> (index) << " is ready");
      return nullptr;
    }
  Ptr<MmWaveComponentCarrierEnb> enbCarrier = DynamicCast<MmWaveComponentCarrierEnb> (carrier->second);
  if (enbCarrier == nullptr)
    {
      NS_LOG_UNCOND ("MmWaveEnbNetDevice: component carrier has unexpected type index="
                     << static_cast<uint32_t> (index));
      return nullptr;
    }
  return enbCarrier->GetPhy ();
}

uint16_t
MmWaveEnbNetDevice::GetCellId () const
{
  NS_LOG_FUNCTION (this);
  return m_cellId;
}

std::map<uint16_t, Ptr<UeManager>>
MmWaveEnbNetDevice::GetUeMap ()
{
  return m_rrc->GetUeMap ();
}

bool
MmWaveEnbNetDevice::HasCellId (uint16_t cellId) const
{
  for (auto &it : m_ccMap)
    {
      if (DynamicCast<MmWaveComponentCarrierEnb> (it.second)->GetCellId () == cellId)
        {
          return true;
        }
    }
  return false;
}

uint8_t
MmWaveEnbNetDevice::GetBandwidth () const
{
  NS_LOG_FUNCTION (this);
  return m_Bandwidth;
}

void
MmWaveEnbNetDevice::SetBandwidth (uint8_t bw)
{
  NS_LOG_FUNCTION (this << bw);
  m_Bandwidth = bw;
}

Ptr<MmWaveEnbMac>
MmWaveEnbNetDevice::GetMac (void)
{
  return DynamicCast<MmWaveComponentCarrierEnb> (m_ccMap.at (0))->GetMac ();
}

Ptr<MmWaveEnbMac>
MmWaveEnbNetDevice::GetMac (uint8_t index)
{
  return DynamicCast<MmWaveComponentCarrierEnb> (m_ccMap.at (index))->GetMac ();
}

void
MmWaveEnbNetDevice::SetRrc (Ptr<LteEnbRrc> rrc)
{
  m_rrc = rrc;
}

Ptr<LteEnbRrc>
MmWaveEnbNetDevice::GetRrc (void)
{
  return m_rrc;
}

bool
MmWaveEnbNetDevice::DoSend (Ptr<Packet> packet, const Address &dest, uint16_t protocolNumber)
{
  NS_LOG_FUNCTION (this << packet << dest << protocolNumber);
  NS_ABORT_MSG_IF (protocolNumber != Ipv4L3Protocol::PROT_NUMBER &&
                       protocolNumber != Ipv6L3Protocol::PROT_NUMBER,
                   "unsupported protocol " << protocolNumber << ", only IPv4/IPv6 is supported");
  return m_rrc->SendData (packet);
}

void
MmWaveEnbNetDevice::UpdateConfig (void)
{
  NS_LOG_FUNCTION (this);

  if (m_isConstructed)
    {
      if (!m_isConfigured)
        {
          NS_LOG_LOGIC (this << " Configure cell " << m_cellId);
          // we have to make sure that this function is called only once
          //m_rrc->ConfigureCell (m_Bandwidth, m_Bandwidth, m_Earfcn, m_Earfcn, m_cellId);
          NS_ASSERT (!m_ccMap.empty ());

          // create the MmWaveComponentCarrierConf map used for the RRC setup
          std::map<uint8_t, LteEnbRrc::MmWaveComponentCarrierConf> ccConfMap;
          for (auto it = m_ccMap.begin (); it != m_ccMap.end (); ++it)
            {
              Ptr<MmWaveComponentCarrierEnb> ccEnb =
                  DynamicCast<MmWaveComponentCarrierEnb> (it->second);
              LteEnbRrc::MmWaveComponentCarrierConf ccConf;
              ccConf.m_ccId = ccEnb->GetConfigurationParameters ()->GetCcId ();
              ccConf.m_cellId = ccEnb->GetCellId ();
              ccConf.m_bandwidth = ccEnb->GetBandwidthInRb ();

              ccConfMap[it->first] = ccConf;
            }

          m_rrc->ConfigureCell (ccConfMap);

          // trigger E2Termination activation for when the simulation starts
          // schedule at start time
          if (m_e2term)
            {
              NS_LOG_DEBUG ("E2sim start in cell " << m_cellId << " force CSV logging "
                                                   << m_forceE2FileLogging);
              //
              if(!m_forceE2FileLogging) {
                  Simulator::Schedule (MicroSeconds (0), &E2Termination::Start, m_e2term);
                }
              //
              m_cuUpFileName = "cu-up-cell-" + std::to_string (m_cellId) + ".txt";
              std::ofstream csv{};
              csv.open (m_cuUpFileName.c_str ());
              csv << "timestamp,ueImsiComplete,DRB.PdcpSduDelayDl (cellAverageLatency),"
                     "m_pDCPBytesUL (0),"
                     "m_pDCPBytesDL (cellDlTxVolume),DRB.PdcpSduVolumeDl_Filter.UEID (txBytes),"
                     "Tot.PdcpSduNbrDl.UEID (txDlPackets),DRB.PdcpSduBitRateDl.UEID"
                     "(pdcpThroughput),"
                     "DRB.PdcpSduDelayDl.UEID (pdcpLatency),QosFlow.PdcpPduVolumeDL_Filter.UEID"
                     "(txPdcpPduBytesNrRlc),DRB.PdcpPduNbrDl.Qos.UEID (txPdcpPduNrRlc)\n";
              csv.close ();

              m_cuCpFileName = "cu-cp-cell-" + std::to_string (m_cellId) + ".txt";
              csv.open (m_cuCpFileName.c_str ());
              csv << "timestamp,ueImsiComplete,numActiveUes,DRB.EstabSucc.5QI.UEID (numDrb),"
                     "DRB.RelActNbr.5QI.UEID (0),L3 serving Id(m_cellId),UE (imsi),L3 serving "
                     "SINR,"
                     "L3 serving SINR 3gpp,"
                     "L3 neigh Id 1 (cellId),L3 neigh SINR 1,L3 neigh SINR 3gpp 1 "
                     "(convertedSinr),"
                     "L3 neigh Id 2 (cellId),L3 neigh SINR 2,L3 neigh SINR 3gpp 2 "
                     "(convertedSinr),"
                     "L3 neigh Id 3 (cellId),L3 neigh SINR 3,L3 neigh SINR 3gpp 3 "
                     "(convertedSinr),"
                     "L3 neigh Id 4 (cellId),L3 neigh SINR 4,L3 neigh SINR 3gpp 4 "
                     "(convertedSinr),"
                     "L3 neigh Id 5 (cellId),L3 neigh SINR 5,L3 neigh SINR 3gpp 5 "
                     "(convertedSinr),"
                     "L3 neigh Id 6 (cellId),L3 neigh SINR 6,L3 neigh SINR 3gpp 6 "
                     "(convertedSinr),"
                     "L3 neigh Id 7 (cellId),L3 neigh SINR 7,L3 neigh SINR 3gpp 7 "
                     "(convertedSinr),"
                     "L3 neigh Id 8 (cellId),L3 neigh SINR 8,L3 neigh SINR 3gpp 8 "
                     "(convertedSinr)"
                     "\n";
              csv.close ();

              m_duFileName = "du-cell-" + std::to_string (m_cellId) + ".txt";
              csv.open (m_duFileName.c_str ());

              std::string header_csv = "timestamp,ueImsiComplete,plmId,nrCellId,dlAvailablePrbs,"
                                       "ulAvailablePrbs,qci,dlPrbUsage,ulPrbUsage";

              std::string cell_header =
                  "TB.TotNbrDl.1,TB.TotNbrDlInitial,TB.TotNbrDlInitial.Qpsk,"
                  "TB.TotNbrDlInitial.16Qam,"
                  "TB.TotNbrDlInitial.64Qam,RRU.PrbUsedDl,TB.ErrTotalNbrDl.1,"
                  "QosFlow.PdcpPduVolumeDL_Filter,CARR.PDSCHMCSDist.Bin1,"
                  "CARR.PDSCHMCSDist.Bin2,"
                  "CARR.PDSCHMCSDist.Bin3,CARR.PDSCHMCSDist.Bin4,CARR.PDSCHMCSDist.Bin5,"
                  "CARR.PDSCHMCSDist.Bin6,L1M.RS-SINR.Bin34,L1M.RS-SINR.Bin46, "
                  "L1M.RS-SINR.Bin58,"
                  "L1M.RS-SINR.Bin70,L1M.RS-SINR.Bin82,L1M.RS-SINR.Bin94,L1M.RS-SINR.Bin127,"
                  "DRB.BufferSize.Qos,DRB.MeanActiveUeDl";

              std::string ue_header =
                  "TB.TotNbrDl.1.UEID,TB.TotNbrDlInitial.UEID,TB.TotNbrDlInitial.Qpsk.UEID,"
                  "TB.TotNbrDlInitial.16Qam.UEID,TB.TotNbrDlInitial.64Qam.UEID,"
                  "TB.ErrTotalNbrDl.1.UEID,"
                  "QosFlow.PdcpPduVolumeDL_Filter.UEID,RRU.PrbUsedDl.UEID,"
                  "CARR.PDSCHMCSDist.Bin1.UEID,"
                  "CARR.PDSCHMCSDist.Bin2.UEID,CARR.PDSCHMCSDist.Bin3.UEID,"
                  "CARR.PDSCHMCSDist.Bin4.UEID,"
                  "CARR.PDSCHMCSDist.Bin5.UEID,"
                  "CARR.PDSCHMCSDist.Bin6.UEID,L1M.RS-SINR.Bin34.UEID, L1M.RS-SINR.Bin46.UEID,"
                  "L1M.RS-SINR.Bin58.UEID,L1M.RS-SINR.Bin70.UEID,L1M.RS-SINR.Bin82.UEID,"
                  "L1M.RS-SINR.Bin94.UEID,L1M.RS-SINR.Bin127.UEID,DRB.BufferSize.Qos.UEID,"
                  "DRB.UEThpDl.UEID, DRB.UEThpDlPdcpBased.UEID";

              csv << header_csv + "," + cell_header + "," + ue_header + "\n";
              csv.close ();
              // TODO: Look at RicSubscriptionRequest_rval_s
              std::string plmId = "111";
              std::string gnbId = std::to_string (m_cellId);
              Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUIDu, this, plmId,
                                   m_cellId);
              Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUICuUp, this,
                                   plmId);
              Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUICuCp, this,
                                   plmId);
              // Simulator::Schedule (MicroSeconds (0), &E2Termination::Start, m_e2term);
              if (m_is_reported)
                {

                  Simulator::Schedule (MicroSeconds (500),
                                       &MmWaveEnbNetDevice::BuildAndSendReportMessage, this,
                                       E2Termination::RicSubscriptionRequest_rval_s{});
                }
            }
          m_isConfigured = true;
        }

      //m_rrc->SetCsgId (m_csgId, m_csgIndication);
    }
  else
    {
      /*
      * Lower layers are not ready yet, so do nothing now and expect
      * ``DoInitialize`` to re-invoke this function.
      */
    }
}

void
MmWaveEnbNetDevice::SetCcMap (std::map<uint8_t, Ptr<MmWaveComponentCarrier>> ccm)
{
  NS_ASSERT_MSG (!m_isConfigured, "attempt to set CC map after configuration");
  m_ccMap = ccm;
}

Ptr<E2Termination>
MmWaveEnbNetDevice::GetE2Termination () const
{
  return m_e2term;
}

bool
MmWaveEnbNetDevice::PrepareTasamSchedulerPolicy(uint64_t transactionId,
                                                uint64_t imsi,
                                                uint16_t minDlShareBp,
                                                uint16_t minUlShareBp,
                                                uint16_t surplusWeightBp)
{
  uint16_t rnti = 0;
  for (const auto& ue : m_rrc->GetUeMap())
    {
      if (ue.second->GetImsi() == imsi)
        {
          rnti = ue.first;
          break;
        }
    }
  if (rnti == 0)
    {
      NS_LOG_WARN("TA-SAM prepare rejected: IMSI " << imsi << " is not attached to cell "
                                                    << m_cellId);
      return false;
    }
  bool prepared = false;
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      if (enb == nullptr)
        {
          continue;
        }
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          scheduler->PrepareTasamUePolicy(transactionId, rnti, minDlShareBp,
                                          minUlShareBp, surplusWeightBp);
          prepared = true;
        }
    }
  return prepared;
}

bool
MmWaveEnbNetDevice::CommitTasamSchedulerPolicy(uint64_t transactionId,
                                               uint16_t expectedUes,
                                               uint32_t ttlMs,
                                               uint16_t maxDiscretionaryDlSymbolsBp)
{
  bool found = false;
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          found = true;
          if (!scheduler->CommitTasamPolicy(transactionId, expectedUes, ttlMs,
                                            maxDiscretionaryDlSymbolsBp))
            {
              ClearTasamSchedulerPolicy();
              return false;
            }
        }
    }
  return found;
}

bool
MmWaveEnbNetDevice::SetTasamTxPowerPercent(uint16_t targetCellId, uint16_t powerPercent,
                                           uint64_t transactionId, uint32_t ttlMs)
{
  if (targetCellId != m_cellId || powerPercent > 100 ||
      (powerPercent != 0 && (powerPercent < 25 || powerPercent % 5 != 0)))
    {
      return false;
    }
  // E2 control is monotonic per DU. A delayed packet must never undo a
  // newer decision. Internal bootstrap calls use transactionId=0 and are
  // intentionally allowed to establish the initial state.
  if (transactionId > 0 && m_tasamPowerTransaction > 0 &&
      transactionId < m_tasamPowerTransaction)
    {
      NS_LOG_WARN ("Ignoring stale TA-SAM power transaction " << transactionId
                                                               << " on cell " << m_cellId
                                                               << "; latest applied transaction is "
                                                               << m_tasamPowerTransaction);
      return true;
    }
  Ptr<MmWaveEnbPhy> phy = GetPhy();
  if (phy == nullptr)
    {
      return false;
    }
  if (m_tasamNominalTxPowerDbm < 0.0)
    {
      m_tasamNominalTxPowerDbm = phy->GetTxPower();
    }
  double txPowerDbm = 0.0;
  if (powerPercent == 0)
    {
      // Zero is a coordinated sleep request, not an RF percentage.  The
      // controller must have completed handover/PDCP confirmation before it
      // reaches this method; native energy then records the cell-off state.
      phy->SetTxPower(0.0);
      Ptr<MmWaveRadioEnergyModelEnb> energy = GetObject<MmWaveRadioEnergyModelEnb>();
      if (energy != nullptr)
        {
          energy->SetCellOff(true);
        }
      m_tasamTxPowerPercent = 0;
    }
  else
    {
      txPowerDbm =
          m_tasamNominalTxPowerDbm + 10.0 * std::log10(powerPercent / 100.0);
      phy->SetTxPower(txPowerDbm);
      Ptr<MmWaveRadioEnergyModelEnb> energy = GetObject<MmWaveRadioEnergyModelEnb>();
      if (energy != nullptr)
        {
          energy->SetCellOff(false);
        }
      m_tasamTxPowerPercent = powerPercent;
    }
  m_tasamPowerTransaction = transactionId;
  m_tasamPowerExpiry = transactionId > 0
      ? Simulator::Now () + MilliSeconds (ttlMs)
      : Seconds (0);
  Ptr<MmWaveRadioEnergyModelEnb> energy = GetRadioEnergyModel ();
  // NS_LOG_UNCOND compila para no-op em builds optimized; as duas marcas
  // abaixo sao a evidencia causal minima e por isso usam std::clog direto.
  if (energy != nullptr && powerPercent != 0)
    {
      energy->SetTxPowerPercent(powerPercent);
      std::clog << "TA-SAM energia: celula=" << m_cellId << " pct=" << powerPercent
                << " txId=" << transactionId << " ttlMs=" << ttlMs
                << " (modelo causal atualizado)" << std::endl;
    }
  else if (powerPercent != 0)
    {
      static std::set<uint16_t> warnedCells;
      if (warnedCells.insert (m_cellId).second)
        {
          std::clog << "TA-SAM energia: modelo causal AUSENTE na celula "
                    << m_cellId << "; cortes nao afetarao a energia nativa"
                    << std::endl;
        }
    }
  // Independent native evidence: record only after the PHY and radio-energy
  // state have been updated. The periodic scenario snapshot can stop before
  // its next 100 ms sample when an E2 callback arrives late; this direct row
  // remains separate from command/ACK records and is the confirmation point.
  if (transactionId > 0)
    {
      StringValue energyOutputDirValue;
      std::string nativeOutputDir;
      GlobalValue::GetValueByName ("energyOutputDir", energyOutputDirValue);
      nativeOutputDir = energyOutputDirValue.Get ();
      if (nativeOutputDir.empty ())
        {
          const char* outputDir = std::getenv ("GREENRAN_NS3_ENERGY_OUTPUT_DIR");
          if (outputDir != nullptr)
            {
              nativeOutputDir = outputDir;
            }
        }
      if (!nativeOutputDir.empty ())
        {
          std::ofstream nativeTrace (nativeOutputDir + "/TasamControlObservations.csv",
                                      std::ios_base::out | std::ios_base::app);
          if (nativeTrace.is_open ())
            {
              struct NativeControlContext
              {
                uint64_t sequence = 0;
                uint64_t decisionId = 0;
                std::string correlation;
                std::string campaign;
                std::string generation;
                std::string sleepTransactionId;
              };
              NativeControlContext context;
              const char* contextPath = std::getenv ("GREENRAN_NS3_NATIVE_CONTROL_CONTEXT_PATH");
              if (contextPath != nullptr && contextPath[0] != '\0')
                {
                  std::ifstream contextFile (contextPath);
                  std::string contextLine;
                  std::getline (contextFile, contextLine);
                  while (std::getline (contextFile, contextLine))
                    {
                      std::vector<std::string> fields;
                      std::stringstream contextStream (contextLine);
                      std::string field;
                      while (std::getline (contextStream, field, ','))
                        {
                          fields.push_back (field);
                        }
                      if (fields.size () < 5)
                        {
                          continue;
                        }
                      try
                        {
                          if (std::stoull (fields[0]) != transactionId)
                            {
                              continue;
                            }
                          context.sequence = std::stoull (fields[0]);
                          context.decisionId = std::stoull (fields[1]);
                          context.correlation = fields[2];
                          context.campaign = fields[3];
                          context.generation = fields[4];
                          if (fields.size () > 6)
                            {
                              context.sleepTransactionId = fields[6];
                            }
                        }
                      catch (const std::exception&)
                        {
                          continue;
                        }
                    }
                }
              const char* configuredEvidenceVersion = std::getenv ("GREENRAN_NATIVE_EVIDENCE_VERSION");
              const std::string evidenceVersion =
                configuredEvidenceVersion != nullptr && configuredEvidenceVersion[0] != '\0'
                  ? configuredEvidenceVersion
                  : "v3";
              const char* configuredSourceGeneration = std::getenv ("GREENRAN_NATIVE_SOURCE_GENERATION");
              const std::string sourceGeneration =
                configuredSourceGeneration != nullptr && configuredSourceGeneration[0] != '\0'
                  ? configuredSourceGeneration
                  : "native-v3";
              // This row is a PHY readback only.  Do not copy the power
              // transaction into the scheduler transaction column: the
              // scheduler is independently observed by the periodic state
              // snapshot below.
              nativeTrace << Simulator::Now ().GetSeconds () << ',' << m_cellId << ','
                          << GetActiveTasamTransaction () << ',' << transactionId << ','
                          << GetActiveTasamUeCount () << ',' << powerPercent
                          << ',' << phy->GetTxPower () << ',' << m_tasamNominalTxPowerDbm
                          << ",power_readback," << (GetActiveTasamTransaction () > 0 ? 1 : 0)
                          << ',' << GetTasamPowerExpirySimTime () << ',' << sourceGeneration << ','
                          << GetAssociationEpoch () << ','
                          << GetActiveTasamAllocatedDlSymbols () << ','
                          << GetActiveTasamDlSymbolCapacity () << ','
                          << GetActiveTasamDiscretionaryDlSymbolsBp () << ','
                          << GetActiveTasamDiscretionaryDlSymbolsBp () << ','
                          << GetActiveTasamMandatoryDlSymbols () << ','
                          << GetActiveTasamDiscretionaryDlSymbols () << ','
                          << GetActiveTasamWithheldDlSymbols () << ','
                          << context.sleepTransactionId << ','
                          << (context.campaign.empty () ? GetCampaignId () : context.campaign)
                          << ',' << evidenceVersion << ','
                          << (context.generation.empty () ? sourceGeneration : context.generation)
                          << ',' << context.decisionId << ',' << context.correlation << ','
                          << (context.sequence == 0 ? transactionId : context.sequence)
                          << ",tasam_native_aggregate_v1" << std::endl;
              // A power action is a per-cell native control even when this
              // cell has no attached UE and therefore no active scheduler
              // policy. Emit the state snapshot with the same transaction
              // and correlation so the evidence contract distinguishes
              // power-controlled cells from cells with scheduler policy.
              nativeTrace << Simulator::Now ().GetSeconds () << ',' << m_cellId << ','
                          << GetActiveTasamTransaction () << ',' << transactionId << ','
                          << GetActiveTasamUeCount () << ',' << powerPercent << ','
                          << phy->GetTxPower () << ',' << m_tasamNominalTxPowerDbm
                          << ",state_snapshot," << (GetActiveTasamTransaction () > 0 ? 1 : 0)
                          << ',' << GetTasamPowerExpirySimTime () << ',' << sourceGeneration << ','
                          << GetAssociationEpoch () << ','
                          << GetActiveTasamAllocatedDlSymbols () << ','
                          << GetActiveTasamDlSymbolCapacity () << ','
                          << GetActiveTasamDiscretionaryDlSymbolsBp () << ','
                          << GetActiveTasamDiscretionaryDlSymbolsBp () << ','
                          << GetActiveTasamMandatoryDlSymbols () << ','
                          << GetActiveTasamDiscretionaryDlSymbols () << ','
                          << GetActiveTasamWithheldDlSymbols () << ','
                          << context.sleepTransactionId << ','
                          << (context.campaign.empty () ? GetCampaignId () : context.campaign)
                          << ',' << evidenceVersion << ','
                          << (context.generation.empty () ? sourceGeneration : context.generation)
                          << ',' << context.decisionId << ',' << context.correlation << ','
                          << (context.sequence == 0 ? transactionId : context.sequence)
                          << ",tasam_native_aggregate_v1" << std::endl;
            }
          else
            {
              NS_LOG_UNCOND ("TA-SAM native evidence open failed dir=" << nativeOutputDir);
            }
        }
      else
        {
          NS_LOG_UNCOND ("TA-SAM native evidence output directory unavailable");
        }
    }
  NS_LOG_UNCOND("TA-SAM E2 power cell=" << m_cellId << " percent=" << powerPercent
                                         << " txDbm=" << txPowerDbm);
  return true;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamTransaction(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr && scheduler->IsTasamPolicyActive())
        {
          return scheduler->GetActiveTasamTransaction();
        }
    }
  return 0;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamPowerTransaction(void) const
{
  if (m_tasamPowerTransaction == 0 || Simulator::Now () >= m_tasamPowerExpiry)
    {
      return 0;
    }
  return m_tasamPowerTransaction;
}

uint64_t
MmWaveEnbNetDevice::GetTasamPowerTransaction(void) const
{
  return m_tasamPowerTransaction;
}

bool
MmWaveEnbNetDevice::IsTasamPowerLeaseFresh(void) const
{
  return m_tasamPowerTransaction > 0 &&
         (m_tasamPowerExpiry.IsZero () || Simulator::Now () < m_tasamPowerExpiry);
}

uint16_t
MmWaveEnbNetDevice::GetActiveTasamUeCount(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr && scheduler->IsTasamPolicyActive())
        {
          return scheduler->GetActiveTasamUeCount();
        }
    }
  return 0;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamAllocatedDlSymbols(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamAllocatedDlSymbols();
        }
    }
  return 0;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamDlSymbolCapacity(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamDlSymbolCapacity();
        }
    }
  return 0;
}

uint16_t
MmWaveEnbNetDevice::GetActiveTasamDiscretionaryDlSymbolsBp(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamDiscretionaryDlSymbolsBp();
        }
    }
  return 10000;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamMandatoryDlSymbols(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamMandatoryDlSymbols();
        }
    }
  return 0;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamDiscretionaryDlSymbols(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamDiscretionaryDlSymbols();
        }
    }
  return 0;
}

uint64_t
MmWaveEnbNetDevice::GetActiveTasamWithheldDlSymbols(void) const
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          return scheduler->GetActiveTasamWithheldDlSymbols();
        }
    }
  return 0;
}

uint16_t
MmWaveEnbNetDevice::GetTasamTxPowerPercent(void) const
{
  return m_tasamTxPowerPercent;
}

double
MmWaveEnbNetDevice::GetTasamPowerExpirySimTime(void) const
{
  return m_tasamPowerExpiry.GetSeconds();
}

void
MmWaveEnbNetDevice::SetRadioEnergyModel (Ptr<MmWaveRadioEnergyModelEnb> model)
{
  m_radioEnergyModel = model;
}

Ptr<MmWaveRadioEnergyModelEnb>
MmWaveEnbNetDevice::GetRadioEnergyModel (void) const
{
  if (m_radioEnergyModel != nullptr)
    {
      return m_radioEnergyModel;
    }
  return GetObject<MmWaveRadioEnergyModelEnb> ();
}

std::string
MmWaveEnbNetDevice::GetAssociationEpoch(void) const
{
  // The association trace is versioned by the scenario.  This getter keeps
  // the control trace schema stable until the RRC association exporter is
  // attached to the device.
  return "rrc-epoch-0";
}

std::string
MmWaveEnbNetDevice::GetCampaignId(void) const
{
  const char* value = std::getenv("GREENRAN_CAMPAIGN_ID");
  return value == nullptr ? std::string() : std::string(value);
}

double
MmWaveEnbNetDevice::GetTasamNominalTxPowerDbm(void) const
{
  return m_tasamNominalTxPowerDbm;
}

void
MmWaveEnbNetDevice::ClearTasamSchedulerPolicy(void)
{
  for (const auto& carrier : m_ccMap)
    {
      Ptr<MmWaveComponentCarrierEnb> enb =
          DynamicCast<MmWaveComponentCarrierEnb>(carrier.second);
      Ptr<MmWaveFlexTtiMacScheduler> scheduler =
          enb == nullptr ? nullptr
                         : DynamicCast<MmWaveFlexTtiMacScheduler>(enb->GetMacScheduler());
      if (scheduler != nullptr)
        {
          scheduler->ClearTasamPolicy();
        }
    }
}

void
MmWaveEnbNetDevice::ClearTasamControl(void)
{
  m_tasamPowerTransaction = 0;
  m_tasamPowerExpiry = Seconds (0);
  ClearTasamSchedulerPolicy ();
  SetTasamTxPowerPercent(m_cellId, 100);
}

void
SetBSTX (Ptr<MmWaveEnbPhy> phy, int val, uint16_t cellid, bool m_esON)
{
  printf ("in function");
  if (val == 0)
    {
      NS_LOG_UNCOND ("Cell turned off " << cellid << " ,current ES state:" << m_esON);
    }
  else
    {
      NS_LOG_UNCOND ("Cell turned on " << cellid << " ,current ES state:" << m_esON);
    }
  phy->SetTxPower (val); //set Cell TX power
  if (val == 0)
    {
      phy->SetNoiseFigure (100); //high noise
    }
  else
    {
      phy->SetNoiseFigure (5); //default
    }
}

void
MmWaveEnbNetDevice::ProcessPendingTasamControls ()
{
  // E2 termination invokes ControlMessageReceivedCallback from its own
  // thread.  Never touch ns-3 objects from that callback: move the requests
  // to the simulation thread and apply them at the next native snapshot.
  std::deque<PendingTasamControl> pending;
  {
    std::lock_guard<std::mutex> lock (m_tasamControlMutex);
    pending.swap (m_pendingTasamControls);
  }

  /* E2 requests arrive on a transport thread and can be interleaved across
   * DUs. Processing the raw queue in arrival order allowed a PREPARE for a
   * newer sequence to erase the pending policy of an older sequence before
   * its COMMIT arrived. Group each sequence and apply PREPARE -> COMMIT ->
   * CLEAR -> POWER atomically on the ns-3 thread.  CLEAR precedes POWER so
   * a v4 sleep commit can remove the empty source scheduler policy without
   * its safe 100% reset overwriting the final explicit POWER=0. */
  std::map<uint64_t, std::vector<PendingTasamControl>> byTransaction;
  for (const auto& request : pending)
    {
      byTransaction[request.transactionId].push_back (request);
    }

  for (auto& entry : byTransaction)
    {
      const uint64_t transactionId = entry.first;
      auto& requests = entry.second;
      bool applied = true;
      bool hasPower = false;
      bool powerApplied = true;

      for (const auto& request : requests)
        {
          if (request.kind != PendingTasamControl::PREPARE)
            continue;
          applied = PrepareTasamSchedulerPolicy (transactionId, request.imsi,
                                                 request.minDlShareBp,
                                                 request.minUlShareBp,
                                                 request.surplusWeightBp) && applied;
        }
      for (const auto& request : requests)
        {
          if (request.kind != PendingTasamControl::COMMIT)
            continue;
          applied = CommitTasamSchedulerPolicy (transactionId,
                                                request.expectedUes,
                                                request.ttlMs,
                                                request.maxDiscretionaryDlSymbolsBp) && applied;
        }
      for (const auto& request : requests)
        {
          if (request.kind == PendingTasamControl::CLEAR)
            {
              ClearTasamControl ();
            }
        }
      for (const auto& request : requests)
        {
          if (request.kind != PendingTasamControl::POWER)
            continue;
          hasPower = true;
          const bool requestApplied = SetTasamTxPowerPercent (request.targetCellId,
                                                              request.powerPercent,
                                                              transactionId,
                                                              request.ttlMs);
          powerApplied = requestApplied && powerApplied;
          applied = requestApplied && applied;
        }

      // POWER is a valid independent E2 action. Scheduler PREPARE/COMMIT
      // remains atomic when present, but a power-only request must not be
      // treated as malformed and cleared back to 100%.
      if (!applied)
        {
          NS_LOG_ERROR ("TA-SAM transaction " << transactionId
                                               << " rejected on cell " << m_cellId
                                               << "; restoring safe state");
          // Scheduler PREPARE/COMMIT is independent from POWER.  A failed
          // scheduler renewal must never reset a previously confirmed
          // economic power lease to 100%; only a failed power transaction
          // enters the explicit RF failsafe.
          if (hasPower && !powerApplied)
            {
              SetTasamTxPowerPercent (m_cellId, 100, transactionId,
                                      requests.empty () ? 1000 : requests.front ().ttlMs);
            }
          ClearTasamSchedulerPolicy ();
        }
    }
}

bool
MmWaveEnbNetDevice::ControlMessageReceivedCallback (E2AP_PDU_t *sub_req_pdu)
{
  bool controlAccepted = false;
  NS_LOG_UNCOND ("TA-SAM E2 control callback entered on cell " << m_cellId);
  NS_LOG_DEBUG (
      "\nMmWaveEnbNetDevice::ControlMessageReceivedCallback: Received RIC Control Message");
  // Create RIC Control ACK
  Ptr<RicControlMessage> controlMessage = Create<RicControlMessage> (sub_req_pdu);
  //BIT_STRING_t *bit_string = &controlMessage->m_e2SmRcControlHeaderFormat1->ueID.choice.gNB_UEID->ran_UEID

  NS_LOG_INFO ("After RicControlMessage::RicControlMessage constructor");
  NS_LOG_INFO ("Request ID " << controlMessage->m_ricRequestId.ricRequestorID);
  NS_LOG_INFO ("Request type " << controlMessage->m_e2SmRcControlHeaderFormat1->ric_Style_Type);

  switch (controlMessage->m_e2SmRcControlHeaderFormat1->ric_Style_Type)
    {
      case RicControlMessage::ControlMessageServiceStyle::Radio_Bearer_Control: {
        NS_LOG_UNCOND ("Unsupported RIC Style Type ");
        break;
      }

      case RicControlMessage::ControlMessageServiceStyle::Radio_Resource_Allocation_Control: {
        constexpr long MIN_DL_SHARE_BP = 1;
        constexpr long MIN_UL_SHARE_BP = 2;
        constexpr long SURPLUS_WEIGHT_BP = 3;
        constexpr long TRANSACTION_ID = 4;
        constexpr long EXPECTED_UES = 5;
        constexpr long TTL_MS = 6;
        constexpr long MAX_DISCRETIONARY_DL_SYMBOLS_BP = 9;
        const long action = controlMessage->m_e2SmRcControlHeaderFormat1->ric_ControlAction_ID;
        long transactionId = 0;
        bool ok = controlMessage->GetIntegerParameter(TRANSACTION_ID, transactionId);
        if (action == 1)
          {
            long minDl = 0;
            long minUl = 0;
            long weight = 0;
            const uint64_t imsi = controlMessage->GetUeId();
            ok = ok && controlMessage->GetIntegerParameter(MIN_DL_SHARE_BP, minDl) &&
                 controlMessage->GetIntegerParameter(MIN_UL_SHARE_BP, minUl) &&
                 controlMessage->GetIntegerParameter(SURPLUS_WEIGHT_BP, weight) &&
                 minDl >= 0 && minDl <= 10000 && minUl >= 0 && minUl <= 10000 &&
                 weight > 0 && weight <= 10000;
            if (ok)
              {
                PendingTasamControl request;
                request.kind = PendingTasamControl::PREPARE;
                request.transactionId = static_cast<uint64_t>(transactionId);
                request.imsi = imsi;
                request.minDlShareBp = static_cast<uint16_t>(minDl);
                request.minUlShareBp = static_cast<uint16_t>(minUl);
                request.surplusWeightBp = static_cast<uint16_t>(weight);
                std::lock_guard<std::mutex> lock (m_tasamControlMutex);
                m_pendingTasamControls.push_back (request);
              }
          }
        else if (action == 2)
          {
            long expectedUes = 0;
            long ttlMs = 0;
            long maxDiscretionaryDlSymbolsBp = 10000;
            const bool hasBudget = controlMessage->GetIntegerParameter(
                MAX_DISCRETIONARY_DL_SYMBOLS_BP, maxDiscretionaryDlSymbolsBp);
            ok = ok && controlMessage->GetIntegerParameter(EXPECTED_UES, expectedUes) &&
                 controlMessage->GetIntegerParameter(TTL_MS, ttlMs) && expectedUes > 0 &&
                 expectedUes <= 65535 && ttlMs >= 100 && ttlMs <= 60000 &&
                 (!hasBudget || (maxDiscretionaryDlSymbolsBp >= 0 &&
                                 maxDiscretionaryDlSymbolsBp <= 10000));
            if (ok)
              {
                PendingTasamControl request;
                request.kind = PendingTasamControl::COMMIT;
                request.transactionId = static_cast<uint64_t>(transactionId);
                request.expectedUes = static_cast<uint16_t>(expectedUes);
                request.ttlMs = static_cast<uint32_t>(ttlMs);
                request.maxDiscretionaryDlSymbolsBp =
                    static_cast<uint16_t>(maxDiscretionaryDlSymbolsBp);
                std::lock_guard<std::mutex> lock (m_tasamControlMutex);
                m_pendingTasamControls.push_back (request);
              }
          }
        else if (action == 3)
          {
            // Explicit rollback is idempotent and does not require a prepared policy.
            ok = transactionId > 0;
            if (ok)
              {
                PendingTasamControl request;
                request.kind = PendingTasamControl::CLEAR;
                request.transactionId = static_cast<uint64_t>(transactionId);
                std::lock_guard<std::mutex> lock (m_tasamControlMutex);
                m_pendingTasamControls.push_back (request);
              }
          }
        else
          {
            ok = false;
          }
        if (!ok)
          {
            NS_LOG_ERROR("TA-SAM E2 scheduler transaction rejected before enqueue");
          }
        controlAccepted = ok;
        break;
      }
      case RicControlMessage::ControlMessageServiceStyle::Connected_Mode_Mobility: {

        switch (controlMessage->m_e2SmRcControlHeaderFormat1->ric_ControlAction_ID)
          {
            case RicControlMessage::Connected_Mode_Mobility_Control_Action_ID::Handover_Control: {
              NS_LOG_INFO ("Connected mobility, do the handover");
              // do handover
              UEID_GNB_t *UEgnb = (UEID_GNB_t *) calloc (1, sizeof (UEID_GNB_t));

              UEgnb = controlMessage->m_e2SmRcControlHeaderFormat1->ueID.choice.gNB_UEID;
              uint64_t imsi = {0};
              memcpy (&imsi, UEgnb->ran_UEID->buf, UEgnb->ran_UEID->size);
              // TA-SAM v4 uses the compact target-cell parameter for its
              // drain handovers.  Keep the full E2SM-RC target structure as
              // the backward-compatible path used by the legacy xApp.
              constexpr long TARGET_CELL_ID = 7;
              long compactTargetCellId = 0;
              const bool hasCompactTarget = controlMessage->GetIntegerParameter(
                  TARGET_CELL_ID, compactTargetCellId);
              uint16_t targetCellId = hasCompactTarget
                  ? static_cast<uint16_t>(compactTargetCellId)
                  : controlMessage->GetTargetCell();
              if (targetCellId == 0 || targetCellId > 65535)
                {
                  NS_LOG_WARN("Invalid handover target for UE " << imsi);
                  return false;
                }

              auto ueMap = m_rrc->GetUeMap();
              bool ueFound = false;
              for (auto ue : ueMap)
              {
                if (ue.second->GetImsi() == imsi)
                {
                  ueFound = true;
                  break;
                }
              }
    
              if (!ueFound)
              {
                NS_LOG_WARN("UE " << imsi << " not found in cell " << m_cellId);
                return false;
              }
    
              NS_LOG_INFO("Processing handover for UE " << imsi << " to cell " << targetCellId);    

              
              m_rrc->TakeUeHoControl (imsi);
              if (!m_forceE2FileLogging)
                {
                  Simulator::ScheduleWithContext (1, Seconds (0),
                                                  &LteEnbRrc::PerformE2RCHO, m_rrc,
                                                  imsi,targetCellId);
                }
              else
                {
                  Simulator::Schedule (Seconds (0), &LteEnbRrc::PerformE2RCHO, m_rrc,
                                       imsi,targetCellId);
                }
              controlAccepted = true;
              break;
            }

            case RicControlMessage::Connected_Mode_Mobility_Control_Action_ID::
                Conditional_Handover_Control: {
              NS_LOG_UNCOND ("Unsupported Conditional_Handover_Control ");
              break;
            }
            case RicControlMessage::Connected_Mode_Mobility_Control_Action_ID::
                DAPS_Handover_Control: {
              NS_LOG_UNCOND ("Unsupported DAPS_Handover_Control ");
              break;
            }
            default: {
              NS_LOG_INFO ("Unrecognized Control Action type of RIC Control Message");
              break;
            }
          }
        break;
      }
      case RicControlMessage::ControlMessageServiceStyle::Energy_state: {
        constexpr long TARGET_CELL_ID = 7;
        constexpr long POWER_PERCENT = 8;
        constexpr long TRANSACTION_ID = 4;
        constexpr long TTL_MS = 6;
        constexpr long SLEEP_COMMIT = 10;
        long targetCellId = 0;
        long powerPercent = 0;
        long transactionId = 0;
        long ttlMs = 0;
        long sleepCommit = 0;
        const long action = controlMessage->m_e2SmRcControlHeaderFormat1->ric_ControlAction_ID;
        const bool hasTarget = controlMessage->GetIntegerParameter(TARGET_CELL_ID, targetCellId);
        const bool hasPower = controlMessage->GetIntegerParameter(POWER_PERCENT, powerPercent);
        const bool hasTransaction = controlMessage->GetIntegerParameter(TRANSACTION_ID, transactionId);
        const bool hasTtl = controlMessage->GetIntegerParameter(TTL_MS, ttlMs);
        const bool hasSleepCommit = controlMessage->GetIntegerParameter(SLEEP_COMMIT, sleepCommit);
        bool ok = action == 2 && hasTarget && hasPower && hasTransaction && hasTtl &&
                  targetCellId > 0 && targetCellId <= 65535 &&
                  ((powerPercent >= 25 && powerPercent <= 100 && powerPercent % 5 == 0) ||
                   (powerPercent == 0 && hasSleepCommit && sleepCommit == 1)) &&
                  transactionId > 0 && ttlMs >= 100 && ttlMs <= 60000;
        NS_LOG_UNCOND ("TA-SAM E2 power parse cell=" << m_cellId
                                                       << " action=" << action
                                                       << " target=" << targetCellId
                                                       << " percent=" << powerPercent
                                                       << " transaction=" << transactionId
                                                       << " ttl=" << ttlMs
                                                       << " sleepCommit=" << sleepCommit
                                                       << " fields=" << hasTarget << ":" << hasPower
                                                       << ":" << hasTransaction << ":" << hasTtl
                                                       << " ok=" << (ok ? 1 : 0));
        if (ok)
          {
            PendingTasamControl request;
            request.kind = PendingTasamControl::POWER;
            request.targetCellId = static_cast<uint16_t>(targetCellId);
            request.powerPercent = static_cast<uint16_t>(powerPercent);
            request.transactionId = static_cast<uint64_t>(transactionId);
            request.ttlMs = static_cast<uint32_t>(ttlMs);
            std::lock_guard<std::mutex> lock (m_tasamControlMutex);
            m_pendingTasamControls.push_back (request);
          }
        if (!ok)
          {
            NS_LOG_ERROR("TA-SAM E2 power request rejected before enqueue");
          }
        controlAccepted = ok;
        break;
      }
      default: {
        NS_LOG_INFO ("Unrecognized Ric Style Type of Ric Control Message");
        break;
      }
    }
  return controlAccepted;
}

void
MmWaveEnbNetDevice::SetE2Termination (Ptr<E2Termination> e2term)
{
  m_e2term = e2term;

  NS_LOG_DEBUG ("Register E2SM MmWaveEnbNetDevice");

  if (!m_forceE2FileLogging)
    {
      long m_e2_func_id = long (e2_func_id);
      long m_rc_e2_func_id = long (rc_e2_func_id);
      Ptr<KpmFunctionDescription> kpmFd = Create<KpmFunctionDescription> ();
      e2term->RegisterKpmCallbackToE2Sm (
          m_e2_func_id, kpmFd,
          std::bind (&MmWaveEnbNetDevice::KpmSubscriptionCallback, this, std::placeholders::_1));

      Ptr<RicControlFunctionDescription> ricCtrlFd = Create<RicControlFunctionDescription> ();
      e2term->RegisterSmCallbackToE2Sm (
          m_rc_e2_func_id, ricCtrlFd,
          std::bind (&MmWaveEnbNetDevice::ControlMessageReceivedCallback, this,
                     std::placeholders::_1));

      e2term->RegisterCallbackFunctionToE2Sm (
          1, std::bind (&MmWaveEnbNetDevice::stopSendingAndCancelSchedule, this));
    }
}

std::string
MmWaveEnbNetDevice::GetImsiString (uint64_t imsi)
{
  std::string ueImsi = std::to_string (imsi);
  std::string ueImsiComplete{};
  if (ueImsi.length () == 1)
    {
      ueImsiComplete = "0000" + ueImsi;
    }
  else if (ueImsi.length () == 2)
    {
      ueImsiComplete = "000" + ueImsi;
    }
  else
    {
      ueImsiComplete = "00" + ueImsi;
    }
  return ueImsiComplete;
}

Ptr<KpmIndicationHeader>
MmWaveEnbNetDevice::BuildRicIndicationHeader (std::string plmId, std::string gnbId,
                                              uint16_t nrCellId)
{
  if (!m_forceE2FileLogging)
    {
      KpmIndicationHeader::KpmRicIndicationHeaderValues headerValues;
      headerValues.m_plmId = plmId;
      headerValues.m_gnbId = gnbId;
      headerValues.m_nrCellId = nrCellId;
      auto time = Simulator::Now ();
      uint64_t timestamp = m_startTime + (uint64_t) time.GetMilliSeconds ();
      NS_LOG_DEBUG ("NR plmid " << plmId << " gnbId " << gnbId << " nrCellId " << nrCellId);
      NS_LOG_DEBUG ("Timestamp " << timestamp);
      headerValues.m_timestamp = timestamp;

      Ptr<KpmIndicationHeader> header =
          Create<KpmIndicationHeader> (KpmIndicationHeader::GlobalE2nodeType::gNB, headerValues);

      return header;
    }
  else
    {
      return nullptr;
    }
}

Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildRicIndicationMessageCuUp (std::string plmId)
{
  Ptr<MmWaveIndicationMessageHelper> indicationMessageHelper =
      Create<MmWaveIndicationMessageHelper> (IndicationMessageHelper::IndicationMessageType::CuUp,
                                             m_forceE2FileLogging, m_reducedPmValues);

  // get <rnti, UeManager> map of connected UEs
  auto ueMap = m_rrc->GetUeMap ();
  // gNB-wide PDCP volume in downlink
  double cellDlTxVolume = 0;
  // rx bytes in downlink
  double cellDlRxVolume = 0;

  // sum of the per-user average latency
  double perUserAverageLatencySum = 0;

  std::unordered_map<uint64_t, std::string> uePmString{};

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      // double rxDlPackets = m_e2PdcpStatsCalculator->GetDlRxPackets(imsi, 3); // LCID 3 is used for data
      long txDlPackets =
          m_e2PdcpStatsCalculator->GetDlTxPackets (imsi, 3); // LCID 3 is used for data
      double txBytes =
          m_e2PdcpStatsCalculator->GetDlTxData (imsi, 3) * 8 / 1e3; // in kbit, not byte
      double rxBytes =
          m_e2PdcpStatsCalculator->GetDlRxData (imsi, 3) * 8 / 1e3; // in kbit, not byte
      cellDlTxVolume += txBytes;
      cellDlRxVolume += rxBytes;

      long txPdcpPduNrRlc = 0;
      double txPdcpPduBytesNrRlc = 0;

      auto drbMap = ue.second->GetDrbMap ();
      for (auto drb : drbMap)
        {
          txPdcpPduNrRlc += drb.second->m_rlc->GetTxPacketsInReportingPeriod ();
          txPdcpPduBytesNrRlc += drb.second->m_rlc->GetTxBytesInReportingPeriod ();
          drb.second->m_rlc->ResetRlcCounters ();
        }

      auto rlcMap = ue.second->GetRlcMap (); // secondary-connected RLCs
      for (auto drb : rlcMap)
        {
          txPdcpPduNrRlc += drb.second->m_rlc->GetTxPacketsInReportingPeriod ();
          txPdcpPduBytesNrRlc += drb.second->m_rlc->GetTxBytesInReportingPeriod ();
          drb.second->m_rlc->ResetRlcCounters ();
        }
      txPdcpPduBytesNrRlc *= 8 / 1e3;

      double pdcpLatency = m_e2PdcpStatsCalculator->GetDlDelay (imsi, 3) / 1e5; // unit: x 0.1 ms
      perUserAverageLatencySum += pdcpLatency;

      double pdcpThroughput = txBytes / m_e2Periodicity; // unit kbps
      double pdcpThroughputRx = rxBytes / m_e2Periodicity; // unit kbps

      if (m_drbThrDlPdcpBasedComputationUeid.find (imsi) !=
          m_drbThrDlPdcpBasedComputationUeid.end ())
        {
          m_drbThrDlPdcpBasedComputationUeid.at (imsi) += pdcpThroughputRx;
        }
      else
        {
          m_drbThrDlPdcpBasedComputationUeid[imsi] = pdcpThroughputRx;
        }

      // compute bitrate based on RLC statistics, decoupled from pdcp throughput
      double rlcLatency = m_e2RlcStatsCalculator->GetDlDelay (imsi, 3) / 1e9; // unit: s
      double pduStats =
          m_e2RlcStatsCalculator->GetDlPduSizeStats (imsi, 3)[0] * 8.0 / 1e3; // unit kbit
      double rlcBitrate = (rlcLatency == 0) ? 0 : pduStats / rlcLatency; // unit kbit/s

      m_drbThrDlUeid[imsi] = rlcBitrate;

      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " " << m_cellId << " cell, connected UE with IMSI " << imsi
                    << " ueImsiString " << ueImsiComplete << " txDlPackets " << txDlPackets
                    << " txDlPacketsNr " << txPdcpPduNrRlc << " txBytes " << txBytes << " rxBytes "
                    << rxBytes << " txDlBytesNr " << txPdcpPduBytesNrRlc << " pdcpLatency "
                    << pdcpLatency << " pdcpThroughput " << pdcpThroughput << " rlcBitrate "
                    << rlcBitrate);

      m_e2PdcpStatsCalculator->ResetResultsForImsiLcid (imsi, 3);

      if (!indicationMessageHelper->IsOffline ())
        {
          indicationMessageHelper->AddCuUpUePmItem (ueImsiComplete, txPdcpPduBytesNrRlc,
                                                    txPdcpPduNrRlc);
        }

      uePmString.insert (std::make_pair (imsi, ",,,," + std::to_string (txPdcpPduBytesNrRlc) + "," +
                                                   std::to_string (txPdcpPduNrRlc)));
    }

  if (!indicationMessageHelper->IsOffline ())
    {
      indicationMessageHelper->FillCuUpValues (plmId);
    }

  NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                << " " << m_cellId << " cell volume " << cellDlTxVolume);

  if (m_forceE2FileLogging)
    {
      std::ofstream csv{};
      csv.open (m_cuUpFileName.c_str (), std::ios_base::app);
      if (!csv.is_open ())
        {
          NS_FATAL_ERROR ("Can't open file " << m_cuUpFileName.c_str ());
        }

      uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

      // the string is timestamp, ueImsiComplete, DRB.PdcpSduDelayDl (cellAverageLatency),
      // m_pDCPBytesUL (0), m_pDCPBytesDL (cellDlTxVolume), DRB.PdcpSduVolumeDl_Filter.UEID (txBytes),
      // Tot.PdcpSduNbrDl.UEID (txDlPackets), DRB.PdcpSduBitRateDl.UEID (pdcpThroughput),
      // DRB.PdcpSduDelayDl.UEID (pdcpLatency), QosFlow.PdcpPduVolumeDL_Filter.UEID (txPdcpPduBytesNrRlc),
      // DRB.PdcpPduNbrDl.Qos.UEID (txPdcpPduNrRlc)

      for (auto ue : ueMap)
        {
          uint64_t imsi = ue.second->GetImsi ();
          std::string ueImsiComplete = GetImsiString (imsi);

          auto uePms = uePmString.find (imsi)->second;

          std::string to_print = std::to_string (timestamp) + "," + ueImsiComplete + "," + "," +
                                 "," + "," + uePms + "\n";

          csv << to_print;
        }
      csv.close ();
      return nullptr;
    }
  else
    {
      const auto &subsDetails_r = m_e2term->SubscriptionMapRef ();
      return indicationMessageHelper->CreateIndicationMessage (subsDetails_r);
    }
}

template <typename A, typename B>
std::pair<B, A>
flip_pair (const std::pair<A, B> &p)
{
  return std::pair<B, A> (p.second, p.first);
}

template <typename A, typename B>
std::multimap<B, A>
flip_map (const std::map<A, B> &src)
{
  std::multimap<B, A> dst;
  std::transform (src.begin (), src.end (), std::inserter (dst, dst.begin ()), flip_pair<A, B>);
  return dst;
}

// IMP - SINR - L3
Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildRicIndicationMessageCuCp (std::string plmId)
{
  Ptr<MmWaveIndicationMessageHelper> indicationMessageHelper =
      Create<MmWaveIndicationMessageHelper> (IndicationMessageHelper::IndicationMessageType::CuCp,
                                             m_forceE2FileLogging, m_reducedPmValues);

  auto ueMap = m_rrc->GetUeMap ();

  std::unordered_map<uint64_t, std::string> uePmString{};

  for (auto ue : ueMap)
    {
      // TODO: RANTI usage in case of needing.
      // auto rnti = ue.first;
      // m_rrc->GetRntiFromImsi(imsi);
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      Ptr<MeasurementItemList> ueVal = Create<MeasurementItemList> (ueImsiComplete);

      long numDrb = ue.second->GetDrbMap ().size ();

      if (!m_reducedPmValues)
        {
          ueVal->AddItem<long> ("DRB.EstabSucc.5QI.UEID", numDrb);
          ueVal->AddItem<long> ("DRB.RelActNbr.5QI.UEID", 0); // not modeled in the simulator
        }

      // IMP: create L3 RRC reports

      // for the same cell
      double sinrThisCell = 10 * std::log10 (m_l3sinrMap[imsi][m_cellId]);
      double convertedSinr = L3RrcMeasurements::ThreeGppMapSinr (sinrThisCell);

      Ptr<L3RrcMeasurements> l3RrcMeasurementServing;
      if (!indicationMessageHelper->IsOffline ())
        {
          // l3RrcMeasurementServing = L3RrcMeasurements::CreateL3RrcUeSpecificSinrServing (
          //     m_cellId, m_cellId, convertedSinr);
          l3RrcMeasurementServing = L3RrcMeasurements::CreateL3RrcUeSpecificSinrServing (
              m_cellId, m_cellId, sinrThisCell);
        }
      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " enbdev " << m_cellId << " UE " << imsi << " L3 serving SINR "
                    << sinrThisCell << " L3 serving SINR 3gpp " << convertedSinr);

      std::string servingStr = std::to_string (numDrb) + "," + std::to_string (0) + "," +
                               std::to_string (m_cellId) + "," + std::to_string (imsi) + "," +
                               std::to_string (sinrThisCell) + "," + std::to_string (convertedSinr);

      // ueVal->AddItem<long> ("enbdev", m_cellId);
      // ueVal->AddItem<long> ("UE", imsi);
      // ueVal->AddItem<long> ("L3-serving-SINR", sinrThisCell);
      // ueVal->AddItem<long> ("L3-serving-SINR-3gpp", convertedSinr);

      // For the neighbors
      // TODO create double map, imsi -> cell -> sinr
      // TODO store at most 8 reports for each UE, as per the standard

      Ptr<L3RrcMeasurements> l3RrcMeasurementNeigh;
      if (!indicationMessageHelper->IsOffline ())
        {
          l3RrcMeasurementNeigh = L3RrcMeasurements::CreateL3RrcUeSpecificSinrNeigh ();
        }
      double sinr;
      std::string neighStr;

      //invert key and value in sortFlipMap, then sort by value
      std::multimap<long double, uint16_t> sortFlipMap = flip_map (m_l3sinrMap[imsi]);
      //new sortFlipMap structure sortFlipMap < sinr, cellId >
      //The assumption is that the first cell in the scenario is always LTE and the rest NR
      uint16_t nNeighbours = E2SM_REPORT_MAX_NEIGH;
      if (m_l3sinrMap[imsi].size () < nNeighbours)
        {
          nNeighbours = m_l3sinrMap[imsi].size () - 1;
        }
      int itIndex = 0;
      // Save only the first E2SM_REPORT_MAX_NEIGH SINR for each UE which represent the best values among all the SINRs detected by all the cells
      for (std::map<long double, uint16_t>::iterator it = --sortFlipMap.end ();
           it != --sortFlipMap.begin () && itIndex < nNeighbours; it--)
        {
          // uint16_t
          long cellId = it->second;
          // if (cellId != m_cellId)
          // {
          // For Serving cell id idnification
          if (cellId == m_cellId)
            {
              cellId *= -1;
            }
          sinr = 10 * std::log10 (it->first); // now SINR is a key due to the sort of the map
          convertedSinr = L3RrcMeasurements::ThreeGppMapSinr (sinr);
          if (!indicationMessageHelper->IsOffline ())
            {
              // l3RrcMeasurementNeigh->AddNeighbourCellMeasurement (cellId, convertedSinr);
              l3RrcMeasurementNeigh->AddNeighbourCellMeasurement (cellId, sinr);
            }
          NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                        << " enbdev " << m_cellId << " UE " << imsi << " L3 neigh " << cellId
                        << " SINR " << sinr << " sinr encoded " << convertedSinr
                        << " first insert");
          neighStr += "," + std::to_string (cellId) + "," + std::to_string (sinr) + "," +
                      std::to_string (convertedSinr);
          itIndex++;
          // }
        }
      for (int i = nNeighbours; i < E2SM_REPORT_MAX_NEIGH; i++)
        {
          neighStr += ",,,";
        }

      uePmString.insert (std::make_pair (imsi, servingStr + neighStr));

      if (!indicationMessageHelper->IsOffline ())
        {
          indicationMessageHelper->AddCuCpUePmItem (ueImsiComplete, numDrb, 0,
                                                    l3RrcMeasurementServing, l3RrcMeasurementNeigh);
        }
    }

  if (!indicationMessageHelper->IsOffline ())
    {
      // Fill CuCp specific fields
      indicationMessageHelper->FillCuCpValues (ueMap.size ()); // Number of Active UEs
    }

  if (m_forceE2FileLogging)
    {
      std::ofstream csv{};
      csv.open (m_cuCpFileName.c_str (), std::ios_base::app);
      if (!csv.is_open ())
        {
          NS_FATAL_ERROR ("Can't open file " << m_cuCpFileName.c_str ());
        }

      NS_LOG_DEBUG ("m_cuCpFileName open " << m_cuCpFileName);

      // the string is timestamp, ueImsiComplete, numActiveUes, DRB.EstabSucc.5QI.UEID (numDrb), DRB.RelActNbr.5QI.UEID (0), L3 serving Id (m_cellId), UE (imsi), L3 serving SINR, L3 serving SINR 3gpp, L3 neigh Id (cellId), L3 neigh Sinr, L3 neigh SINR 3gpp (convertedSinr)
      // The values for L3 neighbour cells are repeated for each neighbour (7 times in this implementation)

      uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

      for (auto ue : ueMap)
        {
          uint64_t imsi = ue.second->GetImsi ();
          std::string ueImsiComplete = GetImsiString (imsi);

          auto uePms = uePmString.find (imsi)->second;

          std::string to_print = std::to_string (timestamp) + "," + ueImsiComplete + "," +
                                 std::to_string (ueMap.size ()) + "," + uePms + "\n";

          NS_LOG_DEBUG (to_print);

          csv << to_print;
        }
      csv.close ();
      return nullptr;
    }
  else
    {
      NS_LOG_DEBUG (" 2. Fill l3RrcMeasurementServing , l3RrcMeasurementNeigh");

      const auto &subsDetails_r = m_e2term->SubscriptionMapRef ();
      return indicationMessageHelper->CreateIndicationMessage (subsDetails_r);
    }
}

uint32_t
MmWaveEnbNetDevice::GetRlcBufferOccupancy (Ptr<LteRlc> rlc) const
{
  if (DynamicCast<LteRlcAm> (rlc))
    {
      return DynamicCast<LteRlcAm> (rlc)->GetTxBufferSize ();
    }
  else if (DynamicCast<LteRlcUm> (rlc))
    {
      return DynamicCast<LteRlcUm> (rlc)->GetTxBufferSize ();
    }
  else if (DynamicCast<LteRlcUmLowLat> (rlc))
    {
      return DynamicCast<LteRlcUmLowLat> (rlc)->GetTxBufferSize ();
    }
  else
    {
      return 0;
    }
}

Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildRicIndicationMessageDu (std::string plmId, uint16_t nrCellId)
{  
  bool local_m_forceE2FileLogging;

            if (m_forceE2FileLogging)
            {
                local_m_forceE2FileLogging = true;
            }
            else
            {
                local_m_forceE2FileLogging = false;
            }
            if (m_e2andlog)
            {
                local_m_forceE2FileLogging = true;
            }
  Ptr<MmWaveIndicationMessageHelper> indicationMessageHelper =
      Create<MmWaveIndicationMessageHelper> (IndicationMessageHelper::IndicationMessageType::Du,
                                             m_forceE2FileLogging, m_reducedPmValues);

  auto ueMap = m_rrc->GetUeMap ();

  uint32_t macPduCellSpecific = 0;
  uint32_t macPduInitialCellSpecific = 0;
  uint32_t macVolumeCellSpecific = 0;
  uint32_t macQpskCellSpecific = 0;
  uint32_t mac16QamCellSpecific = 0;
  uint32_t mac64QamCellSpecific = 0;
  uint32_t macRetxCellSpecific = 0;
  uint32_t macMac04CellSpecific = 0;
  uint32_t macMac59CellSpecific = 0;
  uint32_t macMac1014CellSpecific = 0;
  uint32_t macMac1519CellSpecific = 0;
  uint32_t macMac2024CellSpecific = 0;
  uint32_t macMac2529CellSpecific = 0;

  uint32_t macSinrBin1CellSpecific = 0;
  uint32_t macSinrBin2CellSpecific = 0;
  uint32_t macSinrBin3CellSpecific = 0;
  uint32_t macSinrBin4CellSpecific = 0;
  uint32_t macSinrBin5CellSpecific = 0;
  uint32_t macSinrBin6CellSpecific = 0;
  uint32_t macSinrBin7CellSpecific = 0;

  uint32_t rlcBufferOccupCellSpecific = 0;

  uint32_t macPrbsCellSpecific = 0;

  std::unordered_map<uint64_t, std::string> uePmStringDu{};

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);
      uint16_t rnti = ue.second->GetRnti ();

      uint32_t macPduUe = m_e2DuCalculator->GetMacPduUeSpecific (rnti, m_cellId);

      macPduCellSpecific += macPduUe;

      uint32_t macPduInitialUe =
          m_e2DuCalculator->GetMacPduInitialTransmissionUeSpecific (rnti, m_cellId);
      macPduInitialCellSpecific += macPduInitialUe;

      uint32_t macVolume = m_e2DuCalculator->GetMacVolumeUeSpecific (rnti, m_cellId);
      macVolumeCellSpecific += macVolume;

      uint32_t macQpsk = m_e2DuCalculator->GetMacPduQpskUeSpecific (rnti, m_cellId);
      macQpskCellSpecific += macQpsk;

      uint32_t mac16Qam = m_e2DuCalculator->GetMacPdu16QamUeSpecific (rnti, m_cellId);
      mac16QamCellSpecific += mac16Qam;

      uint32_t mac64Qam = m_e2DuCalculator->GetMacPdu64QamUeSpecific (rnti, m_cellId);
      mac64QamCellSpecific += mac64Qam;

      uint32_t macRetx = m_e2DuCalculator->GetMacPduRetransmissionUeSpecific (rnti, m_cellId);
      macRetxCellSpecific += macRetx;

      // Numerator = (Sum of number of symbols across all rows (TTIs) group by cell ID and UE ID within a given time window)
      double macNumberOfSymbols =
          m_e2DuCalculator->GetMacNumberOfSymbolsUeSpecific (rnti, m_cellId);

      auto phyMac = GetMac ()->GetConfigurationParameters ();
      // Denominator = (Periodicity of the report time window in ms*number of TTIs per ms*14)
      Time reportingWindow =
          Simulator::Now () - m_e2DuCalculator->GetLastResetTime (rnti, m_cellId);
      double denominatorPrb = std::ceil (reportingWindow.GetNanoSeconds () /
                                         phyMac->GetSlotPeriod ().GetNanoSeconds ()) *
                              14;

      NS_LOG_DEBUG ("macNumberOfSymbols " << macNumberOfSymbols << " denominatorPrb "
                                          << denominatorPrb);

      // Average Number of PRBs allocated for the UE = (NR/DR)*139 (where 139 is the total number of PRBs available per NR cell, given numerology 2 with 60 kHz SCS)
      double macPrb = 0;
      if (denominatorPrb != 0)
        {
          macPrb =
              macNumberOfSymbols / denominatorPrb * 139; // TODO fix this for different numerologies
        }
      macPrbsCellSpecific += macPrb;

      uint32_t macMac04 = m_e2DuCalculator->GetMacMcs04UeSpecific (rnti, m_cellId);
      macMac04CellSpecific += macMac04;

      uint32_t macMac59 = m_e2DuCalculator->GetMacMcs59UeSpecific (rnti, m_cellId);
      macMac59CellSpecific += macMac59;

      uint32_t macMac1014 = m_e2DuCalculator->GetMacMcs1014UeSpecific (rnti, m_cellId);
      macMac1014CellSpecific += macMac1014;

      uint32_t macMac1519 = m_e2DuCalculator->GetMacMcs1519UeSpecific (rnti, m_cellId);
      macMac1519CellSpecific += macMac1519;

      uint32_t macMac2024 = m_e2DuCalculator->GetMacMcs2024UeSpecific (rnti, m_cellId);
      macMac2024CellSpecific += macMac2024;

      uint32_t macMac2529 = m_e2DuCalculator->GetMacMcs2529UeSpecific (rnti, m_cellId);
      macMac2529CellSpecific += macMac2529;

      uint32_t macSinrBin1 = m_e2DuCalculator->GetMacSinrBin1UeSpecific (rnti, m_cellId);
      macSinrBin1CellSpecific += macSinrBin1;

      uint32_t macSinrBin2 = m_e2DuCalculator->GetMacSinrBin2UeSpecific (rnti, m_cellId);
      macSinrBin2CellSpecific += macSinrBin2;

      uint32_t macSinrBin3 = m_e2DuCalculator->GetMacSinrBin3UeSpecific (rnti, m_cellId);
      macSinrBin3CellSpecific += macSinrBin3;

      uint32_t macSinrBin4 = m_e2DuCalculator->GetMacSinrBin4UeSpecific (rnti, m_cellId);
      macSinrBin4CellSpecific += macSinrBin4;

      uint32_t macSinrBin5 = m_e2DuCalculator->GetMacSinrBin5UeSpecific (rnti, m_cellId);
      macSinrBin5CellSpecific += macSinrBin5;

      uint32_t macSinrBin6 = m_e2DuCalculator->GetMacSinrBin6UeSpecific (rnti, m_cellId);
      macSinrBin6CellSpecific += macSinrBin6;

      uint32_t macSinrBin7 = m_e2DuCalculator->GetMacSinrBin7UeSpecific (rnti, m_cellId);
      macSinrBin7CellSpecific += macSinrBin7;

      // get buffer occupancy info
      uint32_t rlcBufferOccup = 0;
      auto drbMap = ue.second->GetDrbMap ();
      for (auto drb : drbMap)
        {
          auto rlc = drb.second->m_rlc;
          rlcBufferOccup += GetRlcBufferOccupancy (rlc);
        }
      auto rlcMap = ue.second->GetRlcMap (); // secondary-connected RLCs
      for (auto drb : rlcMap)
        {
          auto rlc = drb.second->m_rlc;
          rlcBufferOccup += GetRlcBufferOccupancy (rlc);
        }
      rlcBufferOccupCellSpecific += rlcBufferOccup;

      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " " << m_cellId << " cell, connected UE with IMSI " << imsi << " rnti "
                    << rnti << " macPduUe " << macPduUe << " macPduInitialUe " << macPduInitialUe
                    << " macVolume " << macVolume << " macQpsk " << macQpsk << " mac16Qam "
                    << mac16Qam << " mac64Qam " << mac64Qam << " macRetx " << macRetx << " macPrb "
                    << macPrb << " macMac04 " << macMac04 << " macMac59 " << macMac59
                    << " macMac1014 " << macMac1014 << " macMac1519 " << macMac1519
                    << " macMac2024 " << macMac2024 << " macMac2529 " << macMac2529
                    << " macSinrBin1 " << macSinrBin1 << " macSinrBin2 " << macSinrBin2
                    << " macSinrBin3 " << macSinrBin3 << " macSinrBin4 " << macSinrBin4
                    << " macSinrBin5 " << macSinrBin5 << " macSinrBin6 " << macSinrBin6
                    << " macSinrBin7 " << macSinrBin7 << " rlcBufferOccup " << rlcBufferOccup);

      // UE-specific Downlink IP combined EN-DC throughput from LTE eNB. Unit is kbps. Pdcp based computation
      // This value is not requested anymore, so it has been removed from the delivery, but it will be still logged;
      double drbThrDlPdcpBasedUeid = m_drbThrDlPdcpBasedComputationUeid.find (imsi) !=
                                             m_drbThrDlPdcpBasedComputationUeid.end ()
                                         ? m_drbThrDlPdcpBasedComputationUeid.at (imsi)
                                         : 0;

      // UE-specific Downlink IP combined EN-DC throughput from LTE eNB. Unit is kbps. Rlc based computation
      double drbThrDlUeid =
          m_drbThrDlUeid.find (imsi) != m_drbThrDlUeid.end () ? m_drbThrDlUeid.at (imsi) : 0;

      indicationMessageHelper->AddDuUePmItem (
          ueImsiComplete, macPduUe, macPduInitialUe, macQpsk, mac16Qam, mac64Qam, macRetx,
          macVolume, macPrb, macMac04, macMac59, macMac1014, macMac1519, macMac2024, macMac2529,
          macSinrBin1, macSinrBin2, macSinrBin3, macSinrBin4, macSinrBin5, macSinrBin6, macSinrBin7,
          rlcBufferOccup, drbThrDlUeid);

      uePmStringDu.insert (std::make_pair (
          imsi, std::to_string (macPduUe) + "," + std::to_string (macPduInitialUe) + "," +
                    std::to_string (macQpsk) + "," + std::to_string (mac16Qam) + "," +
                    std::to_string (mac64Qam) + "," + std::to_string (macRetx) + "," +
                    std::to_string (macVolume) + "," + std::to_string (macPrb) + "," +
                    std::to_string (macMac04) + "," + std::to_string (macMac59) + "," +
                    std::to_string (macMac1014) + "," + std::to_string (macMac1519) + "," +
                    std::to_string (macMac2024) + "," + std::to_string (macMac2529) + "," +
                    std::to_string (macSinrBin1) + "," + std::to_string (macSinrBin2) + "," +
                    std::to_string (macSinrBin3) + "," + std::to_string (macSinrBin4) + "," +
                    std::to_string (macSinrBin5) + "," + std::to_string (macSinrBin6) + "," +
                    std::to_string (macSinrBin7) + "," + std::to_string (rlcBufferOccup) + ',' +
                    std::to_string (drbThrDlUeid) + ',' + std::to_string (drbThrDlPdcpBasedUeid)));

      // reset UE
      m_e2DuCalculator->ResetPhyTracesForRntiCellId (rnti, m_cellId);
    }
  m_drbThrDlPdcpBasedComputationUeid.clear ();
  m_drbThrDlUeid.clear ();

  // Denominator = (Total number of rows (TTIs) within a given time window* 14)
  // Numerator = (Sum of number of symbols across all rows (TTIs) group by cell ID within a given time window) * 139
  // Average Number of PRBs allocated for the UE = (NR/DR) (where 139 is the total number of PRBs available per NR cell, given numerology 2 with 60 kHz SCS)
  double prbUtilizationDl = macPrbsCellSpecific;

  NS_LOG_DEBUG (
      Simulator::Now ().GetSeconds ()
      << " " << m_cellId << " cell, connected UEs number " << ueMap.size ()
      << " macPduCellSpecific " << macPduCellSpecific << " macPduInitialCellSpecific "
      << macPduInitialCellSpecific << " macVolumeCellSpecific " << macVolumeCellSpecific
      << " macQpskCellSpecific " << macQpskCellSpecific << " mac16QamCellSpecific "
      << mac16QamCellSpecific << " mac64QamCellSpecific " << mac64QamCellSpecific
      << " macRetxCellSpecific " << macRetxCellSpecific << " macPrbsCellSpecific "
      << macPrbsCellSpecific //<< " " << macNumberOfSymbolsCellSpecific << " " << denominatorPrb
      << " macMac04CellSpecific " << macMac04CellSpecific << " macMac59CellSpecific "
      << macMac59CellSpecific << " macMac1014CellSpecific " << macMac1014CellSpecific
      << " macMac1519CellSpecific " << macMac1519CellSpecific << " macMac2024CellSpecific "
      << macMac2024CellSpecific << " macMac2529CellSpecific " << macMac2529CellSpecific
      << " macSinrBin1CellSpecific " << macSinrBin1CellSpecific << " macSinrBin2CellSpecific "
      << macSinrBin2CellSpecific << " macSinrBin3CellSpecific " << macSinrBin3CellSpecific
      << " macSinrBin4CellSpecific " << macSinrBin4CellSpecific << " macSinrBin5CellSpecific "
      << macSinrBin5CellSpecific << " macSinrBin6CellSpecific " << macSinrBin6CellSpecific
      << " macSinrBin7CellSpecific " << macSinrBin7CellSpecific);

  long dlAvailablePrbs = 139; // TODO this is for the current configuration, make it configurable
  long ulAvailablePrbs = 139; // TODO this is for the current configuration, make it configurable
  long qci = 1;
  long dlPrbUsage = std::min ((long) (prbUtilizationDl / dlAvailablePrbs * 100),
                              (long) 100); // percentage of used PRBs
  long ulPrbUsage = 0; // TODO for future implementation

  if (!indicationMessageHelper->IsOffline ())
    {
      indicationMessageHelper->AddDuCellPmItem (
          macPduCellSpecific, macPduInitialCellSpecific, macQpskCellSpecific, mac16QamCellSpecific,
          mac64QamCellSpecific, prbUtilizationDl, macRetxCellSpecific, macVolumeCellSpecific,
          macMac04CellSpecific, macMac59CellSpecific, macMac1014CellSpecific,
          macMac1519CellSpecific, macMac2024CellSpecific, macMac2529CellSpecific,
          macSinrBin1CellSpecific, macSinrBin2CellSpecific, macSinrBin3CellSpecific,
          macSinrBin4CellSpecific, macSinrBin5CellSpecific, macSinrBin6CellSpecific,
          macSinrBin7CellSpecific, rlcBufferOccupCellSpecific, ueMap.size ());

      Ptr<CellResourceReport> cellResRep = Create<CellResourceReport> ();
      cellResRep->m_plmId = plmId;
      cellResRep->m_nrCellId = nrCellId;
      cellResRep->dlAvailablePrbs = dlAvailablePrbs;
      cellResRep->ulAvailablePrbs = ulAvailablePrbs;

      Ptr<ServedPlmnPerCell> servedPlmnPerCell = Create<ServedPlmnPerCell> ();
      servedPlmnPerCell->m_plmId = plmId;
      servedPlmnPerCell->m_nrCellId = nrCellId;

      Ptr<EpcDuPmContainer> epcDuVal = Create<EpcDuPmContainer> ();
      epcDuVal->m_qci = qci;
      epcDuVal->m_dlPrbUsage = dlPrbUsage;
      epcDuVal->m_ulPrbUsage = ulPrbUsage;

      servedPlmnPerCell->m_perQciReportItems.insert (epcDuVal);
      cellResRep->m_servedPlmnPerCellItems.insert (servedPlmnPerCell);

      indicationMessageHelper->AddDuCellResRepPmItem (cellResRep);
      indicationMessageHelper->FillDuValues (plmId + std::to_string (nrCellId));
    }

  if (m_forceE2FileLogging)
    {
      std::ofstream csv{};
      csv.open (m_duFileName.c_str (), std::ios_base::app);
      if (!csv.is_open ())
        {
          NS_FATAL_ERROR ("Can't open file " << m_duFileName.c_str ());
        }

      uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

      // the string is timestamp, ueImsiComplete, plmId, nrCellId, dlAvailablePrbs, ulAvailablePrbs, qci , dlPrbUsage, ulPrbUsage, /*CellSpecificValues*/, /* UESpecificValues */

      /*
      CellSpecificValues:
        TB.TotNbrDl.1, TB.TotNbrDlInitial, TB.TotNbrDlInitial.Qpsk, TB.TotNbrDlInitial.16Qam, TB.TotNbrDlInitial.64Qam, RRU.PrbUsedDl,
        TB.ErrTotalNbrDl.1, QosFlow.PdcpPduVolumeDL_Filter, CARR.PDSCHMCSDist.Bin1, CARR.PDSCHMCSDist.Bin2, CARR.PDSCHMCSDist.Bin3,
        CARR.PDSCHMCSDist.Bin4, CARR.PDSCHMCSDist.Bin5, CARR.PDSCHMCSDist.Bin6, L1M.RS-SINR.Bin34, L1M.RS-SINR.Bin46, L1M.RS-SINR.Bin58,
        L1M.RS-SINR.Bin70, L1M.RS-SINR.Bin82, L1M.RS-SINR.Bin94, L1M.RS-SINR.Bin127, DRB.BufferSize.Qos, DRB.MeanActiveUeDl
    */

      std::string to_print_cell =
          plmId + "," + std::to_string (nrCellId) + "," + std::to_string (dlAvailablePrbs) + "," +
          std::to_string (ulAvailablePrbs) + "," + std::to_string (qci) + "," +
          std::to_string (dlPrbUsage) + "," + std::to_string (ulPrbUsage) + "," +
          std::to_string (macPduCellSpecific) + "," + std::to_string (macPduInitialCellSpecific) +
          "," + std::to_string (macQpskCellSpecific) + "," + std::to_string (mac16QamCellSpecific) +
          "," + std::to_string (mac64QamCellSpecific) + "," +
          std::to_string ((long) std::ceil (prbUtilizationDl)) + "," +
          std::to_string (macRetxCellSpecific) + "," + std::to_string (macVolumeCellSpecific) +
          "," + std::to_string (macMac04CellSpecific) + "," +
          std::to_string (macMac59CellSpecific) + "," + std::to_string (macMac1014CellSpecific) +
          "," + std::to_string (macMac1519CellSpecific) + "," +
          std::to_string (macMac2024CellSpecific) + "," + std::to_string (macMac2529CellSpecific) +
          "," + std::to_string (macSinrBin1CellSpecific) + "," +
          std::to_string (macSinrBin2CellSpecific) + "," +
          std::to_string (macSinrBin3CellSpecific) + "," +
          std::to_string (macSinrBin4CellSpecific) + "," +
          std::to_string (macSinrBin5CellSpecific) + "," +
          std::to_string (macSinrBin6CellSpecific) + "," +
          std::to_string (macSinrBin7CellSpecific) + "," +
          std::to_string (rlcBufferOccupCellSpecific) + "," + std::to_string (ueMap.size ());

      for (auto ue : ueMap)
        {
          uint64_t imsi = ue.second->GetImsi ();
          std::string ueImsiComplete = GetImsiString (imsi);

          auto uePms = uePmStringDu.find (imsi)->second;

          std::string to_print = std::to_string (timestamp) + "," + ueImsiComplete + "," +
                                 to_print_cell + "," + uePms + "\n";

          csv << to_print;
        }
      csv.close ();

      return nullptr;
    }
  else
    {
      const auto &subsDetails_r = m_e2term->SubscriptionMapRef ();
      return indicationMessageHelper->CreateIndicationMessage (subsDetails_r);
    }
}

void
MmWaveEnbNetDevice::BuildAndSendReportMessage (E2Termination::RicSubscriptionRequest_rval_s params)
{
  std::string plmId = "111";
  std::string gnbId = std::to_string (m_cellId);

  // TODO here we can get something from RRC and onward
  NS_LOG_DEBUG ("MmWaveEnbNetDevice " << m_cellId << " BuildAndSendMessage at time "
                                      << Simulator::Now ().GetSeconds ());

  if (m_sendCuUp)
    {
      // Create CU-UP
      Ptr<KpmIndicationHeader> header = BuildRicIndicationHeader (plmId, gnbId, m_cellId);
      Ptr<KpmIndicationMessage> cuUpMsg = BuildRicIndicationMessageCuUp (plmId);

      // Send CU-UP only if offline logging is disabled
      if (header != nullptr && cuUpMsg != nullptr)
        {
          NS_LOG_DEBUG ("Send NR CU-UP");
          E2AP_PDU *pdu_cuup_ue = new E2AP_PDU;
          encoding::generate_e2apv1_indication_request_parameterized (
              pdu_cuup_ue, params.requestorId, params.instanceId, params.ranFuncionId,
              params.actionId,
              1, // TODO sequence number
              (uint8_t *) header->m_buffer, // buffer containing the encoded header
              header->m_size, // size of the encoded header
              (uint8_t *) cuUpMsg->m_buffer, // buffer containing the encoded message
              cuUpMsg->m_size); // size of the encoded message
          m_e2term->SendE2Message (pdu_cuup_ue);
          delete pdu_cuup_ue;
        }
    }

  if (m_sendCuCp)
    {
      // Create and send CU-CP
      Ptr<KpmIndicationHeader> header = BuildRicIndicationHeader (plmId, gnbId, m_cellId);
      Ptr<KpmIndicationMessage> cuCpMsg = BuildRicIndicationMessageCuCp (plmId);

      // Send CU-CP only if offline logging is disabled
      if (header != nullptr && cuCpMsg != nullptr)
        {

          NS_LOG_DEBUG ("Send NR CU-CP");
          E2AP_PDU *pdu_cucp_ue = new E2AP_PDU;
          encoding::generate_e2apv1_indication_request_parameterized (
              pdu_cucp_ue, params.requestorId, params.instanceId, params.ranFuncionId,
              params.actionId,
              1, // TODO sequence number
              (uint8_t *) header->m_buffer, // buffer containing the encoded header
              header->m_size, // size of the encoded header
              (uint8_t *) cuCpMsg->m_buffer, // buffer containing the encoded message
              cuCpMsg->m_size); // size of the encoded message
          m_e2term->SendE2Message (pdu_cucp_ue);
          delete pdu_cucp_ue;
        }
    }

  if (m_sendDu)
    {
      // Create DU
      Ptr<KpmIndicationHeader> header = BuildRicIndicationHeader (plmId, gnbId, m_cellId);
      Ptr<KpmIndicationMessage> duMsg = BuildRicIndicationMessageDu (plmId, m_cellId);

      // Send DU only if offline logging is disabled
      if (header != nullptr && duMsg != nullptr)
        {

          NS_LOG_DEBUG ("Send NR DU");
          E2AP_PDU *pdu_du_ue = new E2AP_PDU;
          encoding::generate_e2apv1_indication_request_parameterized (
              pdu_du_ue, params.requestorId, params.instanceId, params.ranFuncionId,
              params.actionId,
              1, // TODO sequence number
              (uint8_t *) header->m_buffer, // buffer containing the encoded header
              header->m_size, // size of the encoded header
              (uint8_t *) duMsg->m_buffer, // buffer containing the encoded message
              duMsg->m_size); // size of the encoded message
          m_e2term->SendE2Message (pdu_du_ue);
          delete pdu_du_ue;
        }
    }

  if (m_stopSendingMessages)
    {
      return;
    }

  if (!m_stopSendingMessages && m_is_reported)
    {
      // TODO: replace by global system preodicity(GranularityPeriod).
      // uint64_t perodicity = m_e2term->SubscriptionMapRef ().find ("Granularity Period") !=
      //                               m_e2term->SubscriptionMapRef ().end ()
      //                           ? m_e2term->SubscriptionMapRef ()["Granularity Period"]
      //                           : m_e2Periodicity;
      Simulator::ScheduleWithContext (1, Seconds (m_e2Periodicity),
                                      &MmWaveEnbNetDevice::BuildAndSendReportMessage, this, params);
    }
}

void
MmWaveEnbNetDevice::SetStartTime (uint64_t st)
{
  m_startTime = st;
}

Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildGUIDu (std::string plmId, uint16_t nrCellId)
{
  auto ueMap = m_rrc->GetUeMap ();

  uint32_t macPduCellSpecific = 0;
  uint32_t macPduInitialCellSpecific = 0;
  uint32_t macVolumeCellSpecific = 0;
  uint32_t macQpskCellSpecific = 0;
  uint32_t mac16QamCellSpecific = 0;
  uint32_t mac64QamCellSpecific = 0;
  uint32_t macRetxCellSpecific = 0;
  uint32_t macMac04CellSpecific = 0;
  uint32_t macMac59CellSpecific = 0;
  uint32_t macMac1014CellSpecific = 0;
  uint32_t macMac1519CellSpecific = 0;
  uint32_t macMac2024CellSpecific = 0;
  uint32_t macMac2529CellSpecific = 0;

  uint32_t macSinrBin1CellSpecific = 0;
  uint32_t macSinrBin2CellSpecific = 0;
  uint32_t macSinrBin3CellSpecific = 0;
  uint32_t macSinrBin4CellSpecific = 0;
  uint32_t macSinrBin5CellSpecific = 0;
  uint32_t macSinrBin6CellSpecific = 0;
  uint32_t macSinrBin7CellSpecific = 0;

  uint32_t rlcBufferOccupCellSpecific = 0;

  uint32_t macPrbsCellSpecific = 0;

  std::unordered_map<uint64_t, std::string> uePmStringDu{};

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);
      uint16_t rnti = ue.second->GetRnti ();

      uint32_t macPduUe = m_e2DuCalculator->GetMacPduUeSpecific (rnti, m_cellId);

      macPduCellSpecific += macPduUe;

      uint32_t macPduInitialUe =
          m_e2DuCalculator->GetMacPduInitialTransmissionUeSpecific (rnti, m_cellId);
      macPduInitialCellSpecific += macPduInitialUe;

      uint32_t macVolume = m_e2DuCalculator->GetMacVolumeUeSpecific (rnti, m_cellId);
      macVolumeCellSpecific += macVolume;

      uint32_t macQpsk = m_e2DuCalculator->GetMacPduQpskUeSpecific (rnti, m_cellId);
      macQpskCellSpecific += macQpsk;

      uint32_t mac16Qam = m_e2DuCalculator->GetMacPdu16QamUeSpecific (rnti, m_cellId);
      mac16QamCellSpecific += mac16Qam;

      uint32_t mac64Qam = m_e2DuCalculator->GetMacPdu64QamUeSpecific (rnti, m_cellId);
      mac64QamCellSpecific += mac64Qam;

      uint32_t macRetx = m_e2DuCalculator->GetMacPduRetransmissionUeSpecific (rnti, m_cellId);
      macRetxCellSpecific += macRetx;

      // Numerator = (Sum of number of symbols across all rows (TTIs) group by cell ID and UE ID within a given time window)
      double macNumberOfSymbols =
          m_e2DuCalculator->GetMacNumberOfSymbolsUeSpecific (rnti, m_cellId);

      auto phyMac = GetMac ()->GetConfigurationParameters ();
      // Denominator = (Periodicity of the report time window in ms*number of TTIs per ms*14)
      Time reportingWindow =
          Simulator::Now () - m_e2DuCalculator->GetLastResetTime (rnti, m_cellId);
      double denominatorPrb = std::ceil (reportingWindow.GetNanoSeconds () /
                                         phyMac->GetSlotPeriod ().GetNanoSeconds ()) *
                              14;

      NS_LOG_DEBUG ("macNumberOfSymbols " << macNumberOfSymbols << " denominatorPrb "
                                          << denominatorPrb);

      // Average Number of PRBs allocated for the UE = (NR/DR)*139 (where 139 is the total number of PRBs available per NR cell, given numerology 2 with 60 kHz SCS)
      double macPrb = 0;
      if (denominatorPrb != 0)
        {
          macPrb =
              macNumberOfSymbols / denominatorPrb * 139; // TODO fix this for different numerologies
        }
      macPrbsCellSpecific += macPrb;

      uint32_t macMac04 = m_e2DuCalculator->GetMacMcs04UeSpecific (rnti, m_cellId);
      macMac04CellSpecific += macMac04;

      uint32_t macMac59 = m_e2DuCalculator->GetMacMcs59UeSpecific (rnti, m_cellId);
      macMac59CellSpecific += macMac59;

      uint32_t macMac1014 = m_e2DuCalculator->GetMacMcs1014UeSpecific (rnti, m_cellId);
      macMac1014CellSpecific += macMac1014;

      uint32_t macMac1519 = m_e2DuCalculator->GetMacMcs1519UeSpecific (rnti, m_cellId);
      macMac1519CellSpecific += macMac1519;

      uint32_t macMac2024 = m_e2DuCalculator->GetMacMcs2024UeSpecific (rnti, m_cellId);
      macMac2024CellSpecific += macMac2024;

      uint32_t macMac2529 = m_e2DuCalculator->GetMacMcs2529UeSpecific (rnti, m_cellId);
      macMac2529CellSpecific += macMac2529;

      uint32_t macSinrBin1 = m_e2DuCalculator->GetMacSinrBin1UeSpecific (rnti, m_cellId);
      macSinrBin1CellSpecific += macSinrBin1;

      uint32_t macSinrBin2 = m_e2DuCalculator->GetMacSinrBin2UeSpecific (rnti, m_cellId);
      macSinrBin2CellSpecific += macSinrBin2;

      uint32_t macSinrBin3 = m_e2DuCalculator->GetMacSinrBin3UeSpecific (rnti, m_cellId);
      macSinrBin3CellSpecific += macSinrBin3;

      uint32_t macSinrBin4 = m_e2DuCalculator->GetMacSinrBin4UeSpecific (rnti, m_cellId);
      macSinrBin4CellSpecific += macSinrBin4;

      uint32_t macSinrBin5 = m_e2DuCalculator->GetMacSinrBin5UeSpecific (rnti, m_cellId);
      macSinrBin5CellSpecific += macSinrBin5;

      uint32_t macSinrBin6 = m_e2DuCalculator->GetMacSinrBin6UeSpecific (rnti, m_cellId);
      macSinrBin6CellSpecific += macSinrBin6;

      uint32_t macSinrBin7 = m_e2DuCalculator->GetMacSinrBin7UeSpecific (rnti, m_cellId);
      macSinrBin7CellSpecific += macSinrBin7;

      // get buffer occupancy info
      uint32_t rlcBufferOccup = 0;
      auto drbMap = ue.second->GetDrbMap ();
      for (auto drb : drbMap)
        {
          auto rlc = drb.second->m_rlc;
          rlcBufferOccup += GetRlcBufferOccupancy (rlc);
        }
      auto rlcMap = ue.second->GetRlcMap (); // secondary-connected RLCs
      for (auto drb : rlcMap)
        {
          auto rlc = drb.second->m_rlc;
          rlcBufferOccup += GetRlcBufferOccupancy (rlc);
        }
      rlcBufferOccupCellSpecific += rlcBufferOccup;

      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " " << m_cellId << " cell, connected UE with IMSI " << imsi << " rnti "
                    << rnti << " macPduUe " << macPduUe << " macPduInitialUe " << macPduInitialUe
                    << " macVolume " << macVolume << " macQpsk " << macQpsk << " mac16Qam "
                    << mac16Qam << " mac64Qam " << mac64Qam << " macRetx " << macRetx << " macPrb "
                    << macPrb << " macMac04 " << macMac04 << " macMac59 " << macMac59
                    << " macMac1014 " << macMac1014 << " macMac1519 " << macMac1519
                    << " macMac2024 " << macMac2024 << " macMac2529 " << macMac2529
                    << " macSinrBin1 " << macSinrBin1 << " macSinrBin2 " << macSinrBin2
                    << " macSinrBin3 " << macSinrBin3 << " macSinrBin4 " << macSinrBin4
                    << " macSinrBin5 " << macSinrBin5 << " macSinrBin6 " << macSinrBin6
                    << " macSinrBin7 " << macSinrBin7 << " rlcBufferOccup " << rlcBufferOccup);

      // UE-specific Downlink IP combined EN-DC throughput from LTE eNB. Unit is kbps. Pdcp based computation
      // This value is not requested anymore, so it has been removed from the delivery, but it will be still logged;
      double drbThrDlPdcpBasedUeid = m_drbThrDlPdcpBasedComputationUeid.find (imsi) !=
                                             m_drbThrDlPdcpBasedComputationUeid.end ()
                                         ? m_drbThrDlPdcpBasedComputationUeid.at (imsi)
                                         : 0;

      // UE-specific Downlink IP combined EN-DC throughput from LTE eNB. Unit is kbps. Rlc based computation
      double drbThrDlUeid =
          m_drbThrDlUeid.find (imsi) != m_drbThrDlUeid.end () ? m_drbThrDlUeid.at (imsi) : 0;

      uePmStringDu.insert (std::make_pair (
          imsi, std::to_string (macPduUe) + "," + std::to_string (macPduInitialUe) + "," +
                    std::to_string (macQpsk) + "," + std::to_string (mac16Qam) + "," +
                    std::to_string (mac64Qam) + "," + std::to_string (macRetx) + "," +
                    std::to_string (macVolume) + "," + std::to_string (macPrb) + "," +
                    std::to_string (macMac04) + "," + std::to_string (macMac59) + "," +
                    std::to_string (macMac1014) + "," + std::to_string (macMac1519) + "," +
                    std::to_string (macMac2024) + "," + std::to_string (macMac2529) + "," +
                    std::to_string (macSinrBin1) + "," + std::to_string (macSinrBin2) + "," +
                    std::to_string (macSinrBin3) + "," + std::to_string (macSinrBin4) + "," +
                    std::to_string (macSinrBin5) + "," + std::to_string (macSinrBin6) + "," +
                    std::to_string (macSinrBin7) + "," + std::to_string (rlcBufferOccup) + ',' +
                    std::to_string (drbThrDlUeid) + ',' + std::to_string (drbThrDlPdcpBasedUeid)));

      // reset UE
      m_e2DuCalculator->ResetPhyTracesForRntiCellId (rnti, m_cellId);
    }
  m_drbThrDlPdcpBasedComputationUeid.clear ();
  m_drbThrDlUeid.clear ();

  // Denominator = (Total number of rows (TTIs) within a given time window* 14)
  // Numerator = (Sum of number of symbols across all rows (TTIs) group by cell ID within a given time window) * 139
  // Average Number of PRBs allocated for the UE = (NR/DR) (where 139 is the total number of PRBs available per NR cell, given numerology 2 with 60 kHz SCS)
  double prbUtilizationDl = macPrbsCellSpecific;

  NS_LOG_DEBUG (
      Simulator::Now ().GetSeconds ()
      << " " << m_cellId << " cell, connected UEs number " << ueMap.size ()
      << " macPduCellSpecific " << macPduCellSpecific << " macPduInitialCellSpecific "
      << macPduInitialCellSpecific << " macVolumeCellSpecific " << macVolumeCellSpecific
      << " macQpskCellSpecific " << macQpskCellSpecific << " mac16QamCellSpecific "
      << mac16QamCellSpecific << " mac64QamCellSpecific " << mac64QamCellSpecific
      << " macRetxCellSpecific " << macRetxCellSpecific << " macPrbsCellSpecific "
      << macPrbsCellSpecific //<< " " << macNumberOfSymbolsCellSpecific << " " << denominatorPrb
      << " macMac04CellSpecific " << macMac04CellSpecific << " macMac59CellSpecific "
      << macMac59CellSpecific << " macMac1014CellSpecific " << macMac1014CellSpecific
      << " macMac1519CellSpecific " << macMac1519CellSpecific << " macMac2024CellSpecific "
      << macMac2024CellSpecific << " macMac2529CellSpecific " << macMac2529CellSpecific
      << " macSinrBin1CellSpecific " << macSinrBin1CellSpecific << " macSinrBin2CellSpecific "
      << macSinrBin2CellSpecific << " macSinrBin3CellSpecific " << macSinrBin3CellSpecific
      << " macSinrBin4CellSpecific " << macSinrBin4CellSpecific << " macSinrBin5CellSpecific "
      << macSinrBin5CellSpecific << " macSinrBin6CellSpecific " << macSinrBin6CellSpecific
      << " macSinrBin7CellSpecific " << macSinrBin7CellSpecific);

  long dlAvailablePrbs = 139; // TODO this is for the current configuration, make it configurable
  long ulAvailablePrbs = 139; // TODO this is for the current configuration, make it configurable
  long qci = 1;
  long dlPrbUsage = std::min ((long) (prbUtilizationDl / dlAvailablePrbs * 100),
                              (long) 100); // percentage of used PRBs
  long ulPrbUsage = 0; // TODO for future implementation

  std::ofstream csv{};
  csv.open (m_duFileName.c_str (), std::ios_base::app);
  if (!csv.is_open ())
    {
      NS_FATAL_ERROR ("Can't open file " << m_duFileName.c_str ());
    }

  uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

  // the string is timestamp, ueImsiComplete, plmId, nrCellId, dlAvailablePrbs, ulAvailablePrbs, qci , dlPrbUsage, ulPrbUsage, /*CellSpecificValues*/, /* UESpecificValues */

  /*
      CellSpecificValues:
        TB.TotNbrDl.1, TB.TotNbrDlInitial, TB.TotNbrDlInitial.Qpsk, TB.TotNbrDlInitial.16Qam, TB.TotNbrDlInitial.64Qam, RRU.PrbUsedDl,
        TB.ErrTotalNbrDl.1, QosFlow.PdcpPduVolumeDL_Filter, CARR.PDSCHMCSDist.Bin1, CARR.PDSCHMCSDist.Bin2, CARR.PDSCHMCSDist.Bin3,
        CARR.PDSCHMCSDist.Bin4, CARR.PDSCHMCSDist.Bin5, CARR.PDSCHMCSDist.Bin6, L1M.RS-SINR.Bin34, L1M.RS-SINR.Bin46, L1M.RS-SINR.Bin58,
        L1M.RS-SINR.Bin70, L1M.RS-SINR.Bin82, L1M.RS-SINR.Bin94, L1M.RS-SINR.Bin127, DRB.BufferSize.Qos, DRB.MeanActiveUeDl
    */

  std::string to_print_cell =
      plmId + "," + std::to_string (nrCellId) + "," + std::to_string (dlAvailablePrbs) + "," +
      std::to_string (ulAvailablePrbs) + "," + std::to_string (qci) + "," +
      std::to_string (dlPrbUsage) + "," + std::to_string (ulPrbUsage) + "," +
      std::to_string (macPduCellSpecific) + "," + std::to_string (macPduInitialCellSpecific) + "," +
      std::to_string (macQpskCellSpecific) + "," + std::to_string (mac16QamCellSpecific) + "," +
      std::to_string (mac64QamCellSpecific) + "," +
      std::to_string ((long) std::ceil (prbUtilizationDl)) + "," +
      std::to_string (macRetxCellSpecific) + "," + std::to_string (macVolumeCellSpecific) + "," +
      std::to_string (macMac04CellSpecific) + "," + std::to_string (macMac59CellSpecific) + "," +
      std::to_string (macMac1014CellSpecific) + "," + std::to_string (macMac1519CellSpecific) +
      "," + std::to_string (macMac2024CellSpecific) + "," +
      std::to_string (macMac2529CellSpecific) + "," + std::to_string (macSinrBin1CellSpecific) +
      "," + std::to_string (macSinrBin2CellSpecific) + "," +
      std::to_string (macSinrBin3CellSpecific) + "," + std::to_string (macSinrBin4CellSpecific) +
      "," + std::to_string (macSinrBin5CellSpecific) + "," +
      std::to_string (macSinrBin6CellSpecific) + "," + std::to_string (macSinrBin7CellSpecific) +
      "," + std::to_string (rlcBufferOccupCellSpecific) + "," + std::to_string (ueMap.size ());

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      auto uePms = uePmStringDu.find (imsi)->second;

      std::string to_print = std::to_string (timestamp) + "," + ueImsiComplete + "," +
                             to_print_cell + "," + uePms + "\n";

      csv << to_print;
    }
  csv.close ();
  Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUIDu, this, plmId, m_cellId);

  return nullptr;
}
// IMP - SINR - L3
Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildGUICuCp (std::string plmId)
{

  auto ueMap = m_rrc->GetUeMap ();

  std::unordered_map<uint64_t, std::string> uePmString{};

  for (auto ue : ueMap)
    {
      // TODO: RANTI usage in case of needing.
      // auto rnti = ue.first;
      // m_rrc->GetRntiFromImsi(imsi);
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      Ptr<MeasurementItemList> ueVal = Create<MeasurementItemList> (ueImsiComplete);

      long numDrb = ue.second->GetDrbMap ().size ();

      if (!m_reducedPmValues)
        {
          ueVal->AddItem<long> ("DRB.EstabSucc.5QI.UEID", numDrb);
          ueVal->AddItem<long> ("DRB.RelActNbr.5QI.UEID", 0); // not modeled in the simulator
        }

      // IMP: create L3 RRC reports

      // for the same cell
      double sinrThisCell = 10 * std::log10 (m_l3sinrMap[imsi][m_cellId]);
      double convertedSinr = L3RrcMeasurements::ThreeGppMapSinr (sinrThisCell);

      Ptr<L3RrcMeasurements> l3RrcMeasurementServing;

      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " enbdev " << m_cellId << " UE " << imsi << " L3 serving SINR "
                    << sinrThisCell << " L3 serving SINR 3gpp " << convertedSinr);

      std::string servingStr = std::to_string (numDrb) + "," + std::to_string (0) + "," +
                               std::to_string (m_cellId) + "," + std::to_string (imsi) + "," +
                               std::to_string (sinrThisCell) + "," + std::to_string (convertedSinr);

      // ueVal->AddItem<long> ("enbdev", m_cellId);
      // ueVal->AddItem<long> ("UE", imsi);
      // ueVal->AddItem<long> ("L3-serving-SINR", sinrThisCell);
      // ueVal->AddItem<long> ("L3-serving-SINR-3gpp", convertedSinr);

      // For the neighbors
      // TODO create double map, imsi -> cell -> sinr
      // TODO store at most 8 reports for each UE, as per the standard

      Ptr<L3RrcMeasurements> l3RrcMeasurementNeigh;

      double sinr;
      std::string neighStr;

      //invert key and value in sortFlipMap, then sort by value
      std::multimap<long double, uint16_t> sortFlipMap = flip_map (m_l3sinrMap[imsi]);
      //new sortFlipMap structure sortFlipMap < sinr, cellId >
      //The assumption is that the first cell in the scenario is always LTE and the rest NR
      uint16_t nNeighbours = E2SM_REPORT_MAX_NEIGH;
      if (m_l3sinrMap[imsi].size () < nNeighbours)
        {
          nNeighbours = m_l3sinrMap[imsi].size () - 1;
        }
      int itIndex = 0;
      // Save only the first E2SM_REPORT_MAX_NEIGH SINR for each UE which represent the best values among all the SINRs detected by all the cells
      for (std::map<long double, uint16_t>::iterator it = --sortFlipMap.end ();
           it != --sortFlipMap.begin () && itIndex < nNeighbours; it--)
        {
          // uint16_t
          long cellId = it->second;
          // if (cellId != m_cellId)
          // {
          // For Serving cell id idnification
          if (cellId == m_cellId)
            {
              cellId *= -1;
            }
          sinr = 10 * std::log10 (it->first); // now SINR is a key due to the sort of the map
          convertedSinr = L3RrcMeasurements::ThreeGppMapSinr (sinr);

          NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                        << " enbdev " << m_cellId << " UE " << imsi << " L3 neigh " << cellId
                        << " SINR " << sinr << " sinr encoded " << convertedSinr
                        << " first insert");
          neighStr += "," + std::to_string (cellId) + "," + std::to_string (sinr) + "," +
                      std::to_string (convertedSinr);
          itIndex++;
          // }
        }
      for (int i = nNeighbours; i < E2SM_REPORT_MAX_NEIGH; i++)
        {
          neighStr += ",,,";
        }

      uePmString.insert (std::make_pair (imsi, servingStr + neighStr));
    }
  std::ofstream csv{};
  csv.open (m_cuCpFileName.c_str (), std::ios_base::app);
  if (!csv.is_open ())
    {
      NS_FATAL_ERROR ("Can't open file " << m_cuCpFileName.c_str ());
    }

  NS_LOG_DEBUG ("m_cuCpFileName open " << m_cuCpFileName);

  // the string is timestamp, ueImsiComplete, numActiveUes, DRB.EstabSucc.5QI.UEID (numDrb), DRB.RelActNbr.5QI.UEID (0), L3 serving Id (m_cellId), UE (imsi), L3 serving SINR, L3 serving SINR 3gpp, L3 neigh Id (cellId), L3 neigh Sinr, L3 neigh SINR 3gpp (convertedSinr)
  // The values for L3 neighbour cells are repeated for each neighbour (7 times in this implementation)

  uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      auto uePms = uePmString.find (imsi)->second;

      std::string to_print = std::to_string (timestamp) + "," + ueImsiComplete + "," +
                             std::to_string (ueMap.size ()) + "," + uePms + "\n";

      NS_LOG_DEBUG (to_print);

      csv << to_print;
    }
  csv.close ();
  Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUICuCp, this, plmId);
  return nullptr;
}
Ptr<KpmIndicationMessage>
MmWaveEnbNetDevice::BuildGUICuUp (std::string plmId)
{
  // get <rnti, UeManager> map of connected UEs
  auto ueMap = m_rrc->GetUeMap ();
  // gNB-wide PDCP volume in downlink
  double cellDlTxVolume = 0;
  // rx bytes in downlink
  double cellDlRxVolume = 0;

  // sum of the per-user average latency
  double perUserAverageLatencySum = 0;

  std::unordered_map<uint64_t, std::string> uePmString{};

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      // double rxDlPackets = m_e2PdcpStatsCalculator->GetDlRxPackets(imsi, 3); // LCID 3 is used for data
      long txDlPackets =
          m_e2PdcpStatsCalculator->GetDlTxPackets (imsi, 3); // LCID 3 is used for data
      double txBytes =
          m_e2PdcpStatsCalculator->GetDlTxData (imsi, 3) * 8 / 1e3; // in kbit, not byte
      double rxBytes =
          m_e2PdcpStatsCalculator->GetDlRxData (imsi, 3) * 8 / 1e3; // in kbit, not byte
      cellDlTxVolume += txBytes;
      cellDlRxVolume += rxBytes;

      long txPdcpPduNrRlc = 0;
      double txPdcpPduBytesNrRlc = 0;

      auto drbMap = ue.second->GetDrbMap ();
      for (auto drb : drbMap)
        {
          txPdcpPduNrRlc += drb.second->m_rlc->GetTxPacketsInReportingPeriod ();
          txPdcpPduBytesNrRlc += drb.second->m_rlc->GetTxBytesInReportingPeriod ();
          drb.second->m_rlc->ResetRlcCounters ();
        }

      auto rlcMap = ue.second->GetRlcMap (); // secondary-connected RLCs
      for (auto drb : rlcMap)
        {
          txPdcpPduNrRlc += drb.second->m_rlc->GetTxPacketsInReportingPeriod ();
          txPdcpPduBytesNrRlc += drb.second->m_rlc->GetTxBytesInReportingPeriod ();
          drb.second->m_rlc->ResetRlcCounters ();
        }
      txPdcpPduBytesNrRlc *= 8 / 1e3;

      double pdcpLatency = m_e2PdcpStatsCalculator->GetDlDelay (imsi, 3) / 1e5; // unit: x 0.1 ms
      perUserAverageLatencySum += pdcpLatency;

      double pdcpThroughput = txBytes / m_e2Periodicity; // unit kbps
      double pdcpThroughputRx = rxBytes / m_e2Periodicity; // unit kbps

      if (m_drbThrDlPdcpBasedComputationUeid.find (imsi) !=
          m_drbThrDlPdcpBasedComputationUeid.end ())
        {
          m_drbThrDlPdcpBasedComputationUeid.at (imsi) += pdcpThroughputRx;
        }
      else
        {
          m_drbThrDlPdcpBasedComputationUeid[imsi] = pdcpThroughputRx;
        }

      // compute bitrate based on RLC statistics, decoupled from pdcp throughput
      double rlcLatency = m_e2RlcStatsCalculator->GetDlDelay (imsi, 3) / 1e9; // unit: s
      double pduStats =
          m_e2RlcStatsCalculator->GetDlPduSizeStats (imsi, 3)[0] * 8.0 / 1e3; // unit kbit
      double rlcBitrate = (rlcLatency == 0) ? 0 : pduStats / rlcLatency; // unit kbit/s

      m_drbThrDlUeid[imsi] = rlcBitrate;

      NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                    << " " << m_cellId << " cell, connected UE with IMSI " << imsi
                    << " ueImsiString " << ueImsiComplete << " txDlPackets " << txDlPackets
                    << " txDlPacketsNr " << txPdcpPduNrRlc << " txBytes " << txBytes << " rxBytes "
                    << rxBytes << " txDlBytesNr " << txPdcpPduBytesNrRlc << " pdcpLatency "
                    << pdcpLatency << " pdcpThroughput " << pdcpThroughput << " rlcBitrate "
                    << rlcBitrate);

      m_e2PdcpStatsCalculator->ResetResultsForImsiLcid (imsi, 3);

      uePmString.insert (std::make_pair (imsi, ",,,," + std::to_string (txPdcpPduBytesNrRlc) + "," +
                                                   std::to_string (txPdcpPduNrRlc)));
    }

  NS_LOG_DEBUG (Simulator::Now ().GetSeconds ()
                << " " << m_cellId << " cell volume " << cellDlTxVolume);

  std::ofstream csv{};
  csv.open (m_cuUpFileName.c_str (), std::ios_base::app);
  if (!csv.is_open ())
    {
      NS_FATAL_ERROR ("Can't open file " << m_cuUpFileName.c_str ());
    }

  uint64_t timestamp = m_startTime + (uint64_t) Simulator::Now ().GetMilliSeconds ();

  // the string is timestamp, ueImsiComplete, DRB.PdcpSduDelayDl (cellAverageLatency),
  // m_pDCPBytesUL (0), m_pDCPBytesDL (cellDlTxVolume), DRB.PdcpSduVolumeDl_Filter.UEID (txBytes),
  // Tot.PdcpSduNbrDl.UEID (txDlPackets), DRB.PdcpSduBitRateDl.UEID (pdcpThroughput),
  // DRB.PdcpSduDelayDl.UEID (pdcpLatency), QosFlow.PdcpPduVolumeDL_Filter.UEID (txPdcpPduBytesNrRlc),
  // DRB.PdcpPduNbrDl.Qos.UEID (txPdcpPduNrRlc)

  for (auto ue : ueMap)
    {
      uint64_t imsi = ue.second->GetImsi ();
      std::string ueImsiComplete = GetImsiString (imsi);

      auto uePms = uePmString.find (imsi)->second;

      std::string to_print =
          std::to_string (timestamp) + "," + ueImsiComplete + "," + "," + "," + "," + uePms + "\n";

      csv << to_print;
    }
  csv.close ();
  Simulator::Schedule (MilliSeconds (100), &MmWaveEnbNetDevice::BuildGUICuUp, this, plmId);
  return nullptr;
}
} // namespace mmwave
} // namespace ns3
