#!/usr/bin/env python3
"""
Power Amplifier Control Library
Provides core functionality for monitoring and controlling the GaN RF amplifier
Can be used as a library or as a CLI application
"""

import argparse
import sys
import struct
import time
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

# Configure logging (disabled by default, enable with -v flag)
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('power_amp_lib')

VERBOSE_LOGGING = False

def enable_verbose_logging():
    """Enable verbose (DEBUG) logging for all power amp modules"""
    global VERBOSE_LOGGING
    VERBOSE_LOGGING = True
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger('power_amp_lib').setLevel(logging.DEBUG)
    logging.getLogger('power_amp_gui').setLevel(logging.DEBUG)
    logging.getLogger('comm_modbus').setLevel(logging.DEBUG)

# Communication imports
from comm_modbus import create_modbus_rs485

# Modbus Register Addresses (must match firmware)
REG_SYSTEM_CURRENT = 0
REG_STAGE3_CURRENT = 1
REG_STAGE4_CURRENT = 2
REG_STAGE3_VOLTAGE = 3
REG_STAGE4_VOLTAGE = 4
REG_RF_REF_POWER = 5
REG_RF_OUT_POWER = 6
REG_GATE_VOLTAGE_C3 = 9
REG_GATE_VOLTAGE_C4A = 10
REG_GATE_VOLTAGE_C4B = 11
REG_TEMPERATURE_HIGH = 12
REG_TEMPERATURE_LOW = 13
REG_STAGE4_POWER_HIGH = 14
REG_STAGE4_POWER_LOW = 15
REG_SUPPLY_OVP = 16
REG_SUPPLY_UVP = 17
REG_NEN_STATUS = 18
REG_OVERCURRENT_SYS = 19
REG_OVERCURRENT_C3 = 20
REG_OVERCURRENT_C4 = 21

# OCP Event Counters
REG_OCP_C3_COUNT_HIGH = 56
REG_OCP_C3_COUNT_LOW = 57
REG_OCP_C4_COUNT_HIGH = 58
REG_OCP_C4_COUNT_LOW = 59
REG_FIRMWARE_VERSION = 60  # Firmware version (read-only)

# AGC Status Registers
REG_AGC_IS_ENABLED = 50
REG_AGC_TEMP_THROTTLING = 51
REG_AGC_OVERTEMP_ERROR = 52
REG_AGC_BAD_SWR = 53
REG_AGC_INTEGRAL_ERROR_HIGH = 54
REG_AGC_INTEGRAL_ERROR_LOW = 55

# Power/Gate Enable Registers (Holding)
REG_POWER_EN_C1 = 115
REG_POWER_EN_C2 = 116
REG_POWER_EN_C3 = 117
REG_POWER_EN_C4 = 118
REG_GATE_EN_C3 = 119
REG_GATE_EN_C4A = 120
REG_GATE_EN_C4B = 121

# AGC Control Registers
REG_AGC_ENABLE = 123
REG_AGC_POWER_GOAL_HIGH = 124
REG_AGC_POWER_GOAL_LOW = 125

# Configuration Control
REG_SAVE_CONFIG = 130  # Write 1 to persist config to flash

# Control Registers
REG_RESET_OVERCURRENT = 138
REG_RESET_OCP_COUNTERS = 139
REG_MCU_SOFTWARE_RESET = 140

# Frequency and device configuration (Holding)
REG_RF_OPERATING_FREQ_HIGH = 170  # Operating frequency MHz (32-bit)
REG_RF_OPERATING_FREQ_LOW = 171
REG_MODBUS_ADDRESS = 172           # Modbus slave address (1-247)

# Modbus address limits
MODBUS_ADDRESS_MIN = 1
MODBUS_ADDRESS_MAX = 247

# Power goal limits
POWER_GOAL_MIN_DBM = 30.0
POWER_GOAL_MAX_DBM = 52.4  # User cap

# Dissipated power warning threshold
DISSIPATED_POWER_WARNING_W = 180.0


class PowerAmpError(Exception):
    """Base exception for power amplifier errors"""
    pass


class ConnectionError(PowerAmpError):
    """Connection-related errors"""
    pass


class CommunicationError(PowerAmpError):
    """Communication errors during register read/write"""
    pass


class ValidationError(PowerAmpError):
    """Parameter validation errors"""
    pass


@dataclass
class AmplifierStatus:
    """Data class containing amplifier monitoring data"""
    # System current and voltages
    system_current_ma: float = 0.0
    stage3_current_ma: float = 0.0
    stage4_current_ma: float = 0.0
    stage3_voltage_v: float = 0.0
    stage4_voltage_v: float = 0.0
    
    # Temperature
    temperature_c: float = 0.0
    
    # RF Power
    rf_output_power_dbm: float = 0.0
    rf_reflected_power_dbm: float = 0.0
    
    # Stage 4 dissipated power
    dissipated_power_w: float = 0.0
    dissipated_power_warning: bool = False
    
    # Gate voltages
    gate_c3_v: float = 0.0
    gate_c4a_v: float = 0.0
    gate_c4b_v: float = 0.0
    
    # Status flags
    ovp_ok: bool = True
    uvp_ok: bool = True
    nen_enabled: bool = False
    
    # Overcurrent status
    overcurrent_sys: bool = False
    overcurrent_c3: bool = False
    overcurrent_c4: bool = False
    
    # OCP event counters
    ocp_c3_count: int = 0
    ocp_c4_count: int = 0
    
    # AGC status
    agc_enabled: bool = False
    agc_temp_throttling: bool = False
    agc_overtemp_error: bool = False
    agc_bad_swr: bool = False
    agc_limit_dbm: float = 0.0
    
    # Power enables
    power_en_c1: bool = False
    power_en_c2: bool = False
    power_en_c3: bool = False
    power_en_c4: bool = False
    
    # Gate enables
    gate_en_c3: bool = False
    gate_en_c4a: bool = False
    gate_en_c4b: bool = False
    
    # Connection status
    device_online: bool = False


class PowerAmplifierController:
    """
    Controller class for the GaN RF Power Amplifier
    Provides monitoring and control functionality
    """
    
    def __init__(self):
        self._modbus_client = None
        self._port: Optional[str] = None
        self._baudrate: int = 115200
        self._connected: bool = False
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to device"""
        return self._connected and self._modbus_client is not None
    
    @property
    def port(self) -> Optional[str]:
        """Get current port"""
        return self._port
    
    @property
    def baudrate(self) -> int:
        """Get current baudrate"""
        return self._baudrate
    
    def connect(self, port: str, baudrate: int = 115200) -> bool:
        """
        Connect to the power amplifier
        
        Args:
            port: Serial port (e.g., 'COM5')
            baudrate: Baud rate (default: 115200)
            
        Returns:
            True if connection successful
            
        Raises:
            ConnectionError: If connection fails
        """
        try:
            self._port = port
            self._baudrate = baudrate
            
            self._modbus_client = create_modbus_rs485(port, baudrate, slave_id=1)
            
            if self._modbus_client.connect():
                # Flush any stale data
                try:
                    if self._modbus_client.client:
                        self._modbus_client.client.reset_input_buffer()
                        self._modbus_client.client.reset_output_buffer()
                except:
                    pass
                
                self._connected = True
                return True
            else:
                raise ConnectionError(f"Failed to open port {port}")
                
        except Exception as e:
            self._connected = False
            self._modbus_client = None
            raise ConnectionError(f"Connection failed: {str(e)}")
    
    def disconnect(self) -> None:
        """Disconnect from the power amplifier"""
        if self._modbus_client:
            try:
                if self._modbus_client.client:
                    self._modbus_client.client.close()
            except:
                pass
            self._modbus_client = None
        self._connected = False
    
    def _write_register(self, address: int, value: int) -> None:
        """Write single register"""
        if not self.is_connected:
            raise ConnectionError("Not connected")
        result = self._modbus_client.write_register(address, int(value))
        if result.isError():
            raise CommunicationError(f"Failed to write register {address}")
    
    def _write_int32_registers(self, high_reg: int, value: int) -> None:
        """Write 32-bit signed integer to two consecutive registers"""
        if value < 0:
            value = value + 0x100000000
        high = (value >> 16) & 0xFFFF
        low = value & 0xFFFF
        self._write_register(high_reg, high)
        self._write_register(high_reg + 1, low)
    
    def _read_input_registers(self, address: int, count: int):
        """Read input registers"""
        if not self.is_connected:
            raise ConnectionError("Not connected")
        logger.debug(f"Reading {count} input registers at address {address}")
        result = self._modbus_client.read_input_registers(address, count)
        logger.debug(f"Result type: {type(result).__name__}, isError: {result.isError() if hasattr(result, 'isError') else 'N/A'}")
        if result.isError():
            logger.error(f"Modbus error reading input registers at {address}: {result}")
            raise CommunicationError(f"Failed to read input registers at {address}: {result}")
        logger.debug(f"Got {len(result.registers)} registers: {result.registers[:5]}..." if len(result.registers) > 5 else f"Got registers: {result.registers}")
        return result.registers
    
    def _read_holding_registers(self, address: int, count: int):
        """Read holding registers"""
        if not self.is_connected:
            raise ConnectionError("Not connected")
        logger.debug(f"Reading {count} holding registers at address {address}")
        result = self._modbus_client.read_holding_registers(address, count)
        logger.debug(f"Result type: {type(result).__name__}, isError: {result.isError() if hasattr(result, 'isError') else 'N/A'}")
        if result.isError():
            logger.error(f"Modbus error reading holding registers at {address}: {result}")
            raise CommunicationError(f"Failed to read holding registers at {address}: {result}")
        logger.debug(f"Got {len(result.registers)} registers")
        return result.registers
    
    def _modbus_registers_to_float(self, high: int, low: int) -> float:
        """Convert two Modbus registers to float"""
        packed = struct.pack('>HH', high, low)
        return struct.unpack('>f', packed)[0]
    
    def get_status(self) -> AmplifierStatus:
        """
        Read current amplifier status
        
        Returns:
            AmplifierStatus object with all monitoring values
            
        Raises:
            ConnectionError: If not connected
            CommunicationError: If read fails
        """
        status = AmplifierStatus()
        
        try:
            # Read main monitoring registers (0-21)
            regs = self._read_input_registers(REG_SYSTEM_CURRENT, 22)
            
            # System current and stage currents/voltages
            status.system_current_ma = float(regs[0])  # Already in mA
            status.stage3_current_ma = float(regs[1])
            status.stage4_current_ma = float(regs[2])
            status.stage3_voltage_v = regs[3] / 1000.0  # mV to V
            status.stage4_voltage_v = regs[4] / 1000.0  # mV to V
            
            # RF power (32-bit signed, divide by 1000 for dBm)
            rf_ref_power = (regs[5] << 16) | regs[6]
            if rf_ref_power >= 0x80000000:
                rf_ref_power -= 0x100000000
            rf_out_power = (regs[7] << 16) | regs[8]
            if rf_out_power >= 0x80000000:
                rf_out_power -= 0x100000000
            status.rf_reflected_power_dbm = rf_ref_power / 1000.0
            status.rf_output_power_dbm = rf_out_power / 1000.0
            
            # Gate voltages (signed mV)
            gate_c3_mV = regs[9] if regs[9] < 32768 else regs[9] - 65536
            gate_c4a_mV = regs[10] if regs[10] < 32768 else regs[10] - 65536
            gate_c4b_mV = regs[11] if regs[11] < 32768 else regs[11] - 65536
            status.gate_c3_v = gate_c3_mV / 1000.0
            status.gate_c4a_v = gate_c4a_mV / 1000.0
            status.gate_c4b_v = gate_c4b_mV / 1000.0
            
            # Temperature (IEEE 754 float)
            status.temperature_c = self._modbus_registers_to_float(regs[12], regs[13])
            
            # Status flags
            status.ovp_ok = bool(regs[16])
            status.uvp_ok = bool(regs[17])
            status.nen_enabled = bool(regs[18])
            status.overcurrent_sys = bool(regs[19])
            status.overcurrent_c3 = bool(regs[20])
            status.overcurrent_c4 = bool(regs[21])
            
            # Read Stage 4 dissipated power
            regs_power = self._read_input_registers(REG_STAGE4_POWER_HIGH, 2)
            dissipated_mW = (regs_power[0] << 16) | regs_power[1]
            if dissipated_mW & 0x80000000:
                dissipated_mW -= 0x100000000
            status.dissipated_power_w = dissipated_mW / 1000.0
            status.dissipated_power_warning = status.dissipated_power_w > DISSIPATED_POWER_WARNING_W
            
            # Read OCP counters
            regs_ocp = self._read_input_registers(REG_OCP_C3_COUNT_HIGH, 4)
            status.ocp_c3_count = (regs_ocp[0] << 16) | regs_ocp[1]
            status.ocp_c4_count = (regs_ocp[2] << 16) | regs_ocp[3]
            
            # Read AGC status
            regs_agc = self._read_input_registers(REG_AGC_IS_ENABLED, 6)
            status.agc_enabled = bool(regs_agc[0])
            status.agc_temp_throttling = bool(regs_agc[1])
            status.agc_overtemp_error = bool(regs_agc[2])
            status.agc_bad_swr = bool(regs_agc[3])
            
            # Read AGC power goal from holding registers
            regs_agc_ctrl = self._read_holding_registers(REG_AGC_ENABLE, 3)
            power_goal_dBm1000 = (regs_agc_ctrl[1] << 16) | regs_agc_ctrl[2]
            if power_goal_dBm1000 >= 0x80000000:
                power_goal_dBm1000 -= 0x100000000
            status.agc_limit_dbm = power_goal_dBm1000 / 1000.0
            
            # Read power/gate enables (7 registers starting at REG_POWER_EN_C1)
            regs_enables = self._read_holding_registers(REG_POWER_EN_C1, 7)
            status.power_en_c1 = bool(regs_enables[0])
            status.power_en_c2 = bool(regs_enables[1])
            status.power_en_c3 = bool(regs_enables[2])
            status.power_en_c4 = bool(regs_enables[3])
            status.gate_en_c3 = bool(regs_enables[4])
            status.gate_en_c4a = bool(regs_enables[5])
            status.gate_en_c4b = bool(regs_enables[6])
            
            status.device_online = True
            
        except Exception as e:
            status.device_online = False
            raise
        
        return status
    
    def set_agc_limit(self, power_dbm: float) -> None:
        """
        Set the AGC limit
        
        Args:
            power_dbm: AGC limit in dBm (capped at 52.4 dBm max)
            
        Raises:
            ValidationError: If power value is out of range
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        if power_dbm < POWER_GOAL_MIN_DBM:
            raise ValidationError(f"AGC limit must be >= {POWER_GOAL_MIN_DBM} dBm")
        
        # Cap at maximum user-allowed value
        if power_dbm > POWER_GOAL_MAX_DBM:
            power_dbm = POWER_GOAL_MAX_DBM
        
        # Convert to milli-dBm (int32)
        power_mdbm = int(power_dbm * 1000)
        self._write_int32_registers(REG_AGC_POWER_GOAL_HIGH, power_mdbm)
    
    def get_agc_limit(self) -> float:
        """
        Get the current AGC limit
        
        Returns:
            AGC limit in dBm
        """
        regs = self._read_holding_registers(REG_AGC_POWER_GOAL_HIGH, 2)
        power_goal_dBm1000 = (regs[0] << 16) | regs[1]
        if power_goal_dBm1000 >= 0x80000000:
            power_goal_dBm1000 -= 0x100000000
        return power_goal_dBm1000 / 1000.0
    
    def get_firmware_version(self) -> int:
        """
        Read the device firmware version

        Returns:
            Firmware version number

        Raises:
            ConnectionError: If not connected
            CommunicationError: If read fails
        """
        regs = self._read_input_registers(REG_FIRMWARE_VERSION, 1)
        return regs[0]

    def get_operating_frequency(self) -> int:
        """
        Read the RF operating frequency

        Returns:
            Operating frequency in MHz

        Raises:
            ConnectionError: If not connected
            CommunicationError: If read fails
        """
        regs = self._read_holding_registers(REG_RF_OPERATING_FREQ_HIGH, 2)
        return (regs[0] << 16) | regs[1]

    def set_operating_frequency(self, freq_mhz: int) -> None:
        """
        Set the RF operating frequency

        Args:
            freq_mhz: Operating frequency in MHz

        Raises:
            ValidationError: If frequency is out of range
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        if freq_mhz < 0:
            raise ValidationError("Operating frequency must be >= 0 MHz")
        self._write_int32_registers(REG_RF_OPERATING_FREQ_HIGH, int(freq_mhz))

    def get_modbus_address(self) -> int:
        """
        Read the device Modbus address

        Returns:
            Modbus slave address (1-247)

        Raises:
            ConnectionError: If not connected
            CommunicationError: If read fails
        """
        regs = self._read_holding_registers(REG_MODBUS_ADDRESS, 1)
        return regs[0]

    def set_modbus_address(self, address: int) -> None:
        """
        Set the device Modbus address

        The controller's active slave ID is updated to match so subsequent
        communication continues to work. Call save_config() to persist the
        change across a power cycle.

        Args:
            address: New Modbus slave address (1-247)

        Raises:
            ValidationError: If address is out of range
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        if address < MODBUS_ADDRESS_MIN or address > MODBUS_ADDRESS_MAX:
            raise ValidationError(
                f"Modbus address must be {MODBUS_ADDRESS_MIN}-{MODBUS_ADDRESS_MAX}"
            )
        self._write_register(REG_MODBUS_ADDRESS, int(address))
        # Update the active slave ID so further comms use the new address
        if self._modbus_client is not None:
            self._modbus_client.slave_id = int(address)

    def save_config(self) -> None:
        """
        Persist the current configuration to device flash

        Raises:
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        self._write_register(REG_SAVE_CONFIG, 1)
    
    def reset_overcurrent(self) -> None:
        """
        Reset overcurrent latches
        
        Raises:
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        self._write_register(REG_RESET_OVERCURRENT, 1)
    
    def reset_ocp_counters(self) -> None:
        """
        Reset OCP event counters
        
        Raises:
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        self._write_register(REG_RESET_OCP_COUNTERS, 1)
    
    def mcu_software_reset(self) -> None:
        """
        Trigger MCU software reset
        The device will reboot after this command.
        
        Raises:
            ConnectionError: If not connected
            CommunicationError: If write fails
        """
        self._write_register(REG_MCU_SOFTWARE_RESET, 1)
        # Disconnect since device is rebooting
        time.sleep(0.5)
        self.disconnect()


def format_status(status: AmplifierStatus) -> str:
    """Format amplifier status for display"""
    lines = []
    lines.append("=== Power Amplifier Status ===")
    lines.append(f"Device Online: {'Yes' if status.device_online else 'No'}")
    lines.append("")
    
    lines.append("--- Current & Voltage ---")
    lines.append(f"System Current:  {status.system_current_ma:.0f} mA")
    lines.append(f"Stage 3 Current: {status.stage3_current_ma:.0f} mA")
    lines.append(f"Stage 4 Current: {status.stage4_current_ma:.0f} mA")
    lines.append(f"Stage 3 Voltage: {status.stage3_voltage_v:.2f} V")
    lines.append(f"Stage 4 Voltage: {status.stage4_voltage_v:.2f} V")
    lines.append("")
    
    lines.append("--- RF Power ---")
    lines.append(f"Output Power:    {status.rf_output_power_dbm:.1f} dBm")
    lines.append(f"Reflected Power: {status.rf_reflected_power_dbm:.1f} dBm")
    lines.append(f"AGC Limit:       {status.agc_limit_dbm:.1f} dBm")
    lines.append("")
    
    lines.append("--- Thermal ---")
    lines.append(f"Temperature:     {status.temperature_c:.1f} °C")
    warning = " [WARNING!]" if status.dissipated_power_warning else ""
    lines.append(f"Dissipated Power: {status.dissipated_power_w:.1f} W{warning}")
    lines.append("")
    
    lines.append("--- Gate Voltages ---")
    lines.append(f"Gate C3:  {status.gate_c3_v:.3f} V")
    lines.append(f"Gate C4A: {status.gate_c4a_v:.3f} V")
    lines.append(f"Gate C4B: {status.gate_c4b_v:.3f} V")
    lines.append("")
    
    lines.append("--- Status Flags ---")
    lines.append(f"OVP:  {'OK' if status.ovp_ok else 'FAULT'}")
    lines.append(f"UVP:  {'OK' if status.uvp_ok else 'FAULT'}")
    lines.append(f"nEn:  {'Enabled' if status.nen_enabled else 'Disabled'}")
    lines.append("")
    
    lines.append("--- Overcurrent ---")
    lines.append(f"System: {'FAULT' if status.overcurrent_sys else 'OK'}")
    lines.append(f"C3:     {'FAULT' if status.overcurrent_c3 else 'OK'}")
    lines.append(f"C4:     {'FAULT' if status.overcurrent_c4 else 'OK'}")
    lines.append(f"C3 Events: {status.ocp_c3_count}")
    lines.append(f"C4 Events: {status.ocp_c4_count}")
    lines.append("")
    
    lines.append("--- AGC Status ---")
    lines.append(f"AGC Enabled:      {'Yes' if status.agc_enabled else 'No'}")
    lines.append(f"Temp Throttling:  {'Active' if status.agc_temp_throttling else 'Inactive'}")
    lines.append(f"Overtemp Error:   {'FAULT' if status.agc_overtemp_error else 'OK'}")
    lines.append(f"Bad SWR:          {'FAULT' if status.agc_bad_swr else 'OK'}")
    lines.append("")
    
    lines.append("--- Power Enables ---")
    lines.append(f"C1: {'ON' if status.power_en_c1 else 'OFF'}  C2: {'ON' if status.power_en_c2 else 'OFF'}  C3: {'ON' if status.power_en_c3 else 'OFF'}  C4: {'ON' if status.power_en_c4 else 'OFF'}")
    lines.append("")
    
    lines.append("--- Gate Enables ---")
    lines.append(f"C3: {'ON' if status.gate_en_c3 else 'OFF'}  C4A: {'ON' if status.gate_en_c4a else 'OFF'}  C4B: {'ON' if status.gate_en_c4b else 'OFF'}")
    
    return "\n".join(lines)


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Power Amplifier Control CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --port COM5 --status
  %(prog)s --port COM5 --baudrate 115200 --set-agc-limit 45.0
  %(prog)s --port COM5 --reset-ocp
  %(prog)s --port COM5 --reset-ocp-counters
  %(prog)s --port COM5 --mcu-reset
  %(prog)s --port COM5 --fw-version
  %(prog)s --port COM5 --get-freq
  %(prog)s --port COM5 --set-freq 2450
  %(prog)s --port COM5 --get-address
  %(prog)s --port COM5 --set-address 2
  %(prog)s --port COM5 --save-config

Status Fields (shown with --status):
  Current & Voltage:
    system_current_ma     System total current (mA)
    stage3_current_ma     Stage 3 current (mA)
    stage4_current_ma     Stage 4 current (mA)
    stage3_voltage_v      Stage 3 voltage (V)
    stage4_voltage_v      Stage 4 voltage (V)

  RF Power:
    rf_output_power_dbm   RF output power (dBm)
    rf_reflected_power_dbm RF reflected power (dBm)
    agc_limit_dbm         AGC power limit (dBm)

  Thermal:
    temperature_c         Temperature (°C)
    dissipated_power_w    Stage 4 dissipated power (W)

  Gate Voltages:
    gate_c3_v             Gate C3 voltage (V)
    gate_c4a_v            Gate C4A voltage (V)
    gate_c4b_v            Gate C4B voltage (V)

  Status Flags:
    ovp_ok                Over-voltage protection OK
    uvp_ok                Under-voltage protection OK
    nen_enabled           nEN pin status

  Overcurrent:
    overcurrent_sys       System overcurrent fault
    overcurrent_c3        C3 overcurrent fault
    overcurrent_c4        C4 overcurrent fault
    ocp_c3_count          C3 OCP event counter
    ocp_c4_count          C4 OCP event counter

  AGC Status:
    agc_enabled           AGC enabled state
    agc_temp_throttling   Temperature throttling active
    agc_overtemp_error    Overtemperature error
    agc_bad_swr           Bad SWR detected

  Power Enables:
    power_en_c1-c4        Power enable for stages C1-C4

  Gate Enables:
    gate_en_c3            Gate enable C3
    gate_en_c4a           Gate enable C4A
    gate_en_c4b           Gate enable C4B
"""
    )
    
    # Connection options
    parser.add_argument("--port", "-p", required=True, 
                        help="Serial port (e.g., COM5)")
    parser.add_argument("--baudrate", "-b", type=int, default=115200,
                        help="Baud rate (default: 115200)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose debug logging")
    
    # Commands (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", "-s", action="store_true",
                       help="Read and display amplifier status")
    group.add_argument("--set-agc-limit", type=float, metavar="DBM",
                       help=f"Set AGC limit in dBm (max: {POWER_GOAL_MAX_DBM})")
    group.add_argument("--get-agc-limit", action="store_true",
                       help="Get current AGC limit")
    group.add_argument("--reset-ocp", action="store_true",
                       help="Reset overcurrent latches")
    group.add_argument("--reset-ocp-counters", action="store_true",
                       help="Reset OCP event counters")
    group.add_argument("--mcu-reset", action="store_true",
                       help="Trigger MCU software reset")
    group.add_argument("--fw-version", action="store_true",
                       help="Read device firmware version")
    group.add_argument("--get-freq", action="store_true",
                       help="Get RF operating frequency (MHz)")
    group.add_argument("--set-freq", type=int, metavar="MHZ",
                       help="Set RF operating frequency (MHz)")
    group.add_argument("--get-address", action="store_true",
                       help="Get Modbus address")
    group.add_argument("--set-address", type=int, metavar="ADDR",
                       help="Set Modbus address (1-247)")
    group.add_argument("--save-config", action="store_true",
                       help="Persist configuration to device flash")
    
    args = parser.parse_args()
    
    if args.verbose:
        enable_verbose_logging()
    
    controller = PowerAmplifierController()
    
    try:
        # Connect
        print(f"Connecting to {args.port} at {args.baudrate} baud...")
        controller.connect(args.port, args.baudrate)
        print("Connected.")
        
        # Execute command
        if args.status:
            status = controller.get_status()
            print(format_status(status))
            
        elif args.set_agc_limit is not None:
            effective_power = min(args.set_agc_limit, POWER_GOAL_MAX_DBM)
            if args.set_agc_limit > POWER_GOAL_MAX_DBM:
                print(f"Note: AGC limit capped at {POWER_GOAL_MAX_DBM} dBm")
            controller.set_agc_limit(effective_power)
            print(f"AGC limit set to {effective_power:.1f} dBm")
            
        elif args.get_agc_limit:
            power = controller.get_agc_limit()
            print(f"Current AGC limit: {power:.1f} dBm")
            
        elif args.reset_ocp:
            controller.reset_overcurrent()
            print("Overcurrent latches reset.")
            
        elif args.reset_ocp_counters:
            controller.reset_ocp_counters()
            print("OCP event counters reset.")
            
        elif args.mcu_reset:
            print("Sending MCU reset command...")
            controller.mcu_software_reset()
            print("MCU reset command sent. Device is rebooting.")
            
        elif args.fw_version:
            version = controller.get_firmware_version()
            print(f"Firmware version: v{version}")
            
        elif args.get_freq:
            freq = controller.get_operating_frequency()
            print(f"Operating frequency: {freq} MHz")
            
        elif args.set_freq is not None:
            controller.set_operating_frequency(args.set_freq)
            print(f"Operating frequency set to {args.set_freq} MHz")
            
        elif args.get_address:
            address = controller.get_modbus_address()
            print(f"Modbus address: {address}")
            
        elif args.set_address is not None:
            controller.set_modbus_address(args.set_address)
            print(f"Modbus address set to {args.set_address}. "
                  f"Use --save-config to persist.")
            
        elif args.save_config:
            controller.save_config()
            print("Configuration saved to flash.")
            
    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except CommunicationError as e:
        print(f"Communication error: {e}", file=sys.stderr)
        sys.exit(2)
    except ValidationError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(3)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
