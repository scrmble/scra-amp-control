"""
Modbus Communication Wrapper
Integrates pymodbus with the communication abstraction layer
"""

import logging
from pymodbus.client import ModbusSerialClient
from typing import Optional
from comm_interface import CommInterface, CommBackend, CommManager

logger = logging.getLogger('comm_modbus')

# Detect pymodbus version for API compatibility
import pymodbus
_pymodbus_version = tuple(int(x) for x in pymodbus.__version__.split('.')[:2])
_use_new_api = _pymodbus_version >= (3, 13)  # API changed in 3.13.0
logger.debug(f"pymodbus version {pymodbus.__version__}, using {'new' if _use_new_api else 'legacy'} API")


class ModbusCommWrapper:
    """
    Wrapper for pymodbus that uses the communication abstraction layer
    Provides easy switching between UART and RS-485 for Modbus communication
    """
    
    def __init__(self, backend: CommInterface, slave_id: int = 1):
        """
        Initialize Modbus wrapper
        Args:
            backend: Communication backend (UARTBackend or RS485Backend)
            slave_id: Modbus slave ID (default: 1)
        """
        self._backend = backend
        self._slave_id = slave_id
        self._modbus_client: Optional[ModbusSerialClient] = None
    
    def connect(self) -> bool:
        """Connect to Modbus device"""
        try:
            # Create Modbus client using backend configuration
            backend_type = self._backend.get_backend_type()
            
            # Get port and baudrate from backend
            port = self._backend.port if hasattr(self._backend, 'port') else None
            baudrate = self._backend.baudrate if hasattr(self._backend, 'baudrate') else 115200
            
            if not port:
                raise ValueError("Backend does not provide port information")
            
            # Create Modbus client
            self._modbus_client = ModbusSerialClient(
                port=port,
                baudrate=baudrate,
                timeout=self._backend.timeout,
                parity='N',
                stopbits=1,
                bytesize=8
            )
            
            # Connect
            logger.info(f"Connecting to {port} at {baudrate} baud...")
            success = self._modbus_client.connect()
            
            if success:
                # Log pymodbus version and client info
                import pymodbus
                logger.info(f"pymodbus version: {pymodbus.__version__}")
                logger.info(f"ModbusSerialClient connected, socket type: {type(self._modbus_client.socket).__name__ if hasattr(self._modbus_client, 'socket') else 'unknown'}")
                
                # Set inter-character timeout for reliable multi-register reads
                # This is needed for some devices/USB adapters that have gaps between bytes
                try:
                    if hasattr(self._modbus_client, 'socket') and self._modbus_client.socket:
                        self._modbus_client.socket.inter_byte_timeout = 0.1
                        logger.info(f"Set inter_byte_timeout=0.1 on socket")
                    else:
                        logger.warning(f"Cannot set inter_byte_timeout: socket={getattr(self._modbus_client, 'socket', 'N/A')}")
                except Exception as e:
                    logger.warning(f"Failed to set inter_byte_timeout: {e}")
                    
                print(f"Connected via {self._backend.get_backend_name()}")
            else:
                logger.error(f"Failed to connect to {port}")
            
            return success
            
        except Exception as e:
            print(f"Modbus connection error: {e}")
            return False
    
    def disconnect(self) -> None:
        """Close serial port"""
        if self._modbus_client:
            try:
                self._modbus_client.close()
            except:
                pass
            self._modbus_client = None
    
    def is_connected(self) -> bool:
        """Check if Modbus client exists (Modbus RTU doesn't have persistent connections)"""
        return self._modbus_client is not None
    
    @property
    def client(self) -> Optional[ModbusSerialClient]:
        """Get underlying Modbus client for direct access"""
        return self._modbus_client
    
    @property
    def slave_id(self) -> int:
        """Get slave ID"""
        return self._slave_id
    
    @slave_id.setter
    def slave_id(self, value: int) -> None:
        """Set slave ID"""
        self._slave_id = value
    
    def get_backend_name(self) -> str:
        """Get backend name"""
        return self._backend.get_backend_name()
    
    def get_backend_type(self) -> CommBackend:
        """Get backend type"""
        return self._backend.get_backend_type()
    
    # Convenience methods for common Modbus operations
    
    def read_input_registers(self, address: int, count: int):
        """Read input registers"""
        if not self.is_connected():
            raise RuntimeError("Not connected")
        if _use_new_api:
            return self._modbus_client.read_input_registers(address, count=count, device_id=self._slave_id)
        else:
            return self._modbus_client.read_input_registers(address, count, slave=self._slave_id)
    
    def read_holding_registers(self, address: int, count: int):
        """Read holding registers"""
        if not self.is_connected():
            raise RuntimeError("Not connected")
        if _use_new_api:
            return self._modbus_client.read_holding_registers(address, count=count, device_id=self._slave_id)
        else:
            return self._modbus_client.read_holding_registers(address, count, slave=self._slave_id)
    
    def write_register(self, address: int, value: int):
        """Write single register"""
        if not self.is_connected():
            raise RuntimeError("Not connected")
        if _use_new_api:
            return self._modbus_client.write_register(address, value, device_id=self._slave_id)
        else:
            return self._modbus_client.write_register(address, value, slave=self._slave_id)
    
    def write_registers(self, address: int, values: list):
        """Write multiple registers"""
        if not self.is_connected():
            raise RuntimeError("Not connected")
        if _use_new_api:
            return self._modbus_client.write_registers(address, values, device_id=self._slave_id)
        else:
            return self._modbus_client.write_registers(address, values, slave=self._slave_id)


def create_modbus_uart(port: str, baudrate: int = 115200, slave_id: int = 1) -> ModbusCommWrapper:
    """
    Create Modbus connection using UART backend
    Args:
        port: Serial port (e.g., 'COM11')
        baudrate: Baud rate (default: 115200)
        slave_id: Modbus slave ID (default: 1)
    Returns: ModbusCommWrapper instance
    """
    from comm_uart import UARTBackend
    backend = UARTBackend(port, baudrate)
    return ModbusCommWrapper(backend, slave_id)


def create_modbus_rs485(port: str, baudrate: int = 115200, slave_id: int = 1) -> ModbusCommWrapper:
    """
    Create Modbus connection using RS-485 backend
    Args:
        port: Serial port (e.g., 'COM11')
        baudrate: Baud rate (default: 115200)
        slave_id: Modbus slave ID (default: 1)
    Returns: ModbusCommWrapper instance
    """
    from comm_rs485 import RS485Backend
    backend = RS485Backend(port, baudrate)
    return ModbusCommWrapper(backend, slave_id)
