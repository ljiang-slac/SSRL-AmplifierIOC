#!/usr/bin/env python3
"""
SRS570 Current Preamplifier IOC

A caproto-based EPICS IOC for controlling Stanford Research Systems Model 570
current preamplifier. Supports both serial (RS-232) and TCP (MOXA) connection modes.

Usage:
    python srs570_ioc.py -p 1,2 --mode serial
    python srs570_ioc.py -p 1,2 --mode tcp --tcp-host 192.168.1.100
"""

import serial
import socket
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import argparse
import asyncio
import os
import json
from pathlib import Path
from caproto.server import PVGroup, pvproperty, run
from caproto import ChannelType
from multiprocessing import Process

from config import ServerConfig

# Declare some global variables
LONG_SLEEP = 15
MID_SLEEP = 10
SHORT_SLEEP = 5


class TCPSerialAdapter:
    """
    Adapter class that provides a serial-like interface for TCP connections.
    Used for MOXA TCP mode connections.
    """
    
    def __init__(self, host: str, port: int, timeout: float = 0.5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self._connect()
    
    def _connect(self):
        """Establish TCP connection."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(self.timeout)
        self.socket.connect((self.host, self.port))
    
    def write(self, data: bytes) -> int:
        """Write data to TCP socket."""
        if isinstance(data, str):
            data = data.encode('ascii')
        return self.socket.send(data)
    
    def read(self, size: int = 1024) -> bytes:
        """Read data from TCP socket."""
        try:
            return self.socket.recv(size)
        except socket.timeout:
            return b''
    
    def readline(self) -> bytes:
        """Read a line from TCP socket."""
        data = b''
        while True:
            try:
                char = self.socket.recv(1)
                if not char:
                    break
                data += char
                if char == b'\n':
                    break
            except socket.timeout:
                break
        return data
    
    def flushInput(self):
        """Clear input buffer."""
        self.socket.settimeout(0.01)
        try:
            while self.socket.recv(1024):
                pass
        except (socket.timeout, BlockingIOError):
            pass
        finally:
            self.socket.settimeout(self.timeout)
    
    def flushOutput(self):
        """No-op for TCP (data is sent immediately)."""
        pass
    
    def close(self):
        """Close TCP connection."""
        if self.socket:
            self.socket.close()
    
    def get_settings(self) -> dict:
        """Return connection settings for logging."""
        return {
            'host': self.host,
            'port': self.port,
            'timeout': self.timeout,
            'mode': 'TCP'
        }


class SRS570IOC(PVGroup):
    """
    EPICS IOC for SRS570 Current Preamplifier.
    
    Provides PVs for controlling all amplifier parameters including:
    - Sensitivity settings
    - Input offset current
    - Bias voltage
    - Filter settings
    - Gain mode
    """
    
    # Descriptor/Name PV - use ChannelType.STRING for proper EPICS string handling
    NAME = pvproperty(value="SRS570 Amplifier", dtype=ChannelType.STRING,
                      doc="Amplifier name/descriptor")
    
    SENSITIVITY = pvproperty(value=0.0, doc="Sensitivity index (0-27)")
    SENSITIVITY_CAL_MODE = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['calibrated', 'uncalibrated'], 
        value='calibrated', doc="Sensitivity cal mode: 0=calibrated, 1=uncalibrated"
    )
    SENSITIVITY_VERNIER = pvproperty(
        value=0, doc="Uncalibrated sensitivity vernier (0-100, percent of full scale)"
    )
    INPUT_OFFSET_ON = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['off', 'on'], 
        value='off', doc="Input offset current on/off: 0=off, 1=on"
    )
    INPUT_OFFSET_LEVEL = pvproperty(
        value=0, doc="Input offset current level index (0-29)"
    )
    INPUT_OFFSET_SIGN = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['negative', 'positive'], 
        value='negative', doc="Input offset sign: 0=negative, 1=positive"
    )
    INPUT_OFFSET_CAL_MODE = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['calibrated', 'uncalibrated'], 
        value='calibrated', doc="Input offset cal mode: 0=calibrated, 1=uncalibrated"
    )
    INPUT_OFFSET_VERNIER = pvproperty(
        value=0, doc="Uncalibrated input offset vernier (-1000 to 1000)"
    )
    BIAS_VOLTAGE_ON = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['off', 'on'], 
        value='off', doc="Bias voltage on/off: 0=off, 1=on"
    )
    BIAS_VOLTAGE_LEVEL = pvproperty(
        value=0, doc="Bias voltage level (-5000 to 5000, -5.000 V to +5.000 V)"
    )
    FILTER_TYPE = pvproperty(
        dtype=ChannelType.ENUM, 
        enum_strings=['6dB HP', '12dB HP', '6dB BP', '6dB LP', '12dB LP', 'None'], 
        value='None', doc="Filter type: 0=6dB HP, 1=12dB HP, 2=6dB BP, 3=6dB LP, 4=12dB LP, 5=None"
    )
    FILTER_LP_FREQ = pvproperty(
        value=0, doc="Lowpass filter 3dB frequency index (0-15)"
    )
    FILTER_HP_FREQ = pvproperty(
        value=0, doc="Highpass filter 3dB frequency index (0-11)"
    )
    RESET_OVERLOAD = pvproperty(
        value=0, doc="Reset filter capacitors: write 1 to trigger, remains 0"
    )
    GAIN_MODE = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['Low Noise', 'High Bandwidth', 'Low Drift'], 
        value='Low Noise', doc="Gain mode: 0=Low Noise, 1=High Bandwidth, 2=Low Drift"
    )
    SIGNAL_INVERT = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['non-inverted', 'inverted'], 
        value='non-inverted', doc="Signal invert sense: 0=non-inverted, 1=inverted"
    )
    BLANK_OUTPUT = pvproperty(
        dtype=ChannelType.ENUM, enum_strings=['no blank', 'blank'], 
        value='no blank', doc="Blank front-end output: 0=no blank, 1=blank"
    )
    OVERLOAD_STATUS = pvproperty(
        dtype=ChannelType.STRING, max_length=40, value="Unknown", doc="Overload status (proxy)"
    )
    
    # Renamed from SENSITIVITY_GAIN to GAIN
    GAIN = pvproperty(value=1e-12, doc="Sensitivity gain in V/A (physical value)")
    OFFSET_LEVEL_GAIN = pvproperty(
        value=1e-12, doc="Input offset current level in Amperes (physical value)"
    )

    # Sensitivity map: index -> V/A
    SENSITIVITY_MAP = {
        0: 1e12, 1: 5e11, 2: 2e11, 3: 1e11, 4: 5e10, 5: 2e10, 6: 1e10, 
        7: 5e9, 8: 2e9, 9: 1e9, 10: 5e8, 11: 2e8, 12: 1e8, 13: 5e7, 
        14: 2e7, 15: 1e7, 16: 5e6, 17: 2e6, 18: 1e6, 19: 5e5, 20: 2e5, 
        21: 1e5, 22: 5e4, 23: 2e4, 24: 1e4, 25: 5e3, 26: 2e3, 27: 1e3
    }
    
    # Offset level map: index -> Amperes
    OFFSET_LEVEL_MAP = {
        0: 1e-12, 1: 2e-12, 2: 5e-12, 3: 10e-12, 4: 20e-12, 5: 50e-12, 
        6: 100e-12, 7: 200e-12, 8: 500e-12, 9: 1e-9, 10: 2e-9, 11: 5e-9,
        12: 10e-9, 13: 20e-9, 14: 50e-9, 15: 100e-9, 16: 200e-9, 17: 500e-9, 
        18: 1e-6, 19: 2e-6, 20: 5e-6, 21: 10e-6, 22: 20e-6, 23: 50e-6,
        24: 100e-6, 25: 200e-6, 26: 500e-6, 27: 1e-3, 28: 2e-3, 29: 5e-3
    }

    FILTER_TYPE_MAP = {
        0: "6dB highpass", 1: "12dB highpass", 2: "6dB bandpass", 
        3: "6dB lowpass", 4: "12dB lowpass", 5: "none"
    }

    FILTER_LP_FREQ_MAP = {
        0: 0.03, 1: 0.1, 2: 0.3, 3: 1, 4: 3, 5: 10, 6: 30, 7: 100, 
        8: 300, 9: 1000, 10: 3000, 11: 10000, 12: 30000, 13: 100000, 
        14: 300000, 15: 1000000
    }

    FILTER_HP_FREQ_MAP = {
        0: 0.03, 1: 0.1, 2: 0.3, 3: 1, 4: 3, 5: 10, 6: 30, 7: 100,
        8: 300, 9: 1000, 10: 3000, 11: 10000
    }

    GAIN_MODE_MAP = {
        0: "Low Noise", 1: "High Bandwidth", 2: "Low Drift"
    }

    def __init__(self, port_index, port_config: dict = None, 
                 connection_mode: str = 'serial', tcp_host: str = None,
                 *args, **kwargs):
        """
        Initialize the SRS570 IOC.
        
        Args:
            port_index: The amplifier index (1-4), can be int or string
            port_config: Configuration dictionary for ports
            connection_mode: 'serial' for RS-232 or 'tcp' for MOXA TCP mode
            tcp_host: TCP host address (required if connection_mode is 'tcp')
        """
        if port_config is None:
            raise ValueError("port_config must be provided (from server config)")
        self.port_config = port_config

        # Convert to string for dict lookup (JSON keys are always strings)
        port_key = str(port_index)
        if port_key not in self.port_config:
            raise ValueError(f"Invalid port index {port_index}. Must be in config.")

        self.port_index = port_key
        self.connection_mode = connection_mode
        self.tcp_host = tcp_host
        
        prefix = self.port_config[port_key].get("prefix", f"SRS570_AMP{port_key}:")
        kwargs['prefix'] = prefix
        super().__init__(*args, **kwargs)
        
        self.json_file = Path(f"srs570_amp{port_key}.json")
        self.initial_json_file = Path(f"config/srs570_initial_amp{port_key}.json")
        self._updating_offset = False
        self._updating_sensitivity = False
        self.last_sens_index = int(self.SENSITIVITY.value)

        self.GAIN._data['value'] = self.SENSITIVITY_MAP[int(self.SENSITIVITY.value)]
        self.OFFSET_LEVEL_GAIN._data['value'] = self.OFFSET_LEVEL_MAP[int(self.INPUT_OFFSET_LEVEL.value)]

        if self.json_file.exists():
            os.remove(self.json_file)
            logger.debug(f"Removed existing {self.json_file} to enforce initial values")

        # Initialize connection based on mode
        self._init_connection()

    def _init_connection(self):
        """Initialize the connection based on the selected mode."""
        if self.connection_mode == 'tcp':
            self._init_tcp_connection()
        else:
            self._init_serial_connection()

    def _init_serial_connection(self):
        """Initialize serial (RS-232) connection."""
        self.port = self.port_config[self.port_index]["serial"]
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=9600,
                bytesize=8,
                parity='N',
                stopbits=2,
                timeout=0.5,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            logger.info(f"Serial port {self.port} opened successfully for AMP{self.port_index}")
            logger.debug(f"Port settings: {self.serial.get_settings()}")
        except serial.SerialException as e:
            logger.error(f"Serial port error for AMP{self.port_index}: {e}")
            raise

    def _init_tcp_connection(self):
        """Initialize TCP (MOXA) connection."""
        # Convert port_index to int for default calculation (port_index is a string from JSON keys)
        port_num = int(self.port_index)
        tcp_port = self.port_config[self.port_index].get("tcp_port", 4000 + port_num)
        
        if not self.tcp_host:
            raise ValueError("TCP host must be provided for TCP connection mode")
        
        try:
            self.serial = TCPSerialAdapter(
                host=self.tcp_host,
                port=tcp_port,
                timeout=0.5
            )
            logger.info(f"TCP connection to {self.tcp_host}:{tcp_port} established for AMP{self.port_index}")
            logger.debug(f"Connection settings: {self.serial.get_settings()}")
        except Exception as e:
            logger.error(f"TCP connection error for AMP{self.port_index}: {e}")
            raise

    async def apply_initial_values(self):
        """Apply initial PV values on startup."""
        logger.debug(f"Post-startup PV values: SENSITIVITY={self.SENSITIVITY.value}, "
                    f"INPUT_OFFSET_CAL_MODE={self.INPUT_OFFSET_CAL_MODE.value}")

    def send_command(self, cmd: str, desc: str, retries: int = 3):
        """Send a command to the SRS570."""
        logger.debug(f"send_command for AMP{self.port_index}: cmd={cmd.strip()}, desc={desc}")
        for attempt in range(retries):
            start_time = time.time()
            try:
                self.serial.flushInput()
                self.serial.flushOutput()
                logger.debug(f"Attempt {attempt + 1}/{retries} - Sending: {desc} ({cmd.strip()})")
                self.serial.write(cmd.encode('ascii'))
                logger.debug(f"Sent {cmd.strip()} to AMP{self.port_index}")
                time.sleep(0.1)
                end_time = time.time()
                logger.debug(f"Set command sent, no response expected, took {end_time - start_time:.3f}s")
                return None
            except (serial.SerialException, socket.error) as e:
                logger.error(f"Communication error on attempt {attempt + 1}: {e}")
            time.sleep(0.1)
        logger.error(f"Failed to complete {desc} after {retries} attempts")
        return None

    def query_command(self, cmd: str, desc: str, retries: int = 3) -> str:
        """Send a query command and read the response."""
        logger.debug(f"Query for AMP{self.port_index}: {cmd.strip()} ({desc})")
        for attempt in range(retries):
            try:
                self.serial.flushInput()
                self.serial.write(cmd.encode('ascii'))
                # Read echo if present (discard)
                echo = self.serial.readline().decode('ascii', errors='ignore').strip()
                if echo and echo == cmd.strip().rstrip('?'):
                    logger.debug(f"Discarded echo: '{echo}'")
                # Wait + read real response
                time.sleep(0.2)
                response = self.serial.readline().decode('ascii', errors='ignore').strip()
                # Clean non-printable
                response = ''.join(c for c in response if 32 <= ord(c) <= 126)
                logger.debug(f"Clean response: '{response}'")
                return response or "No response"
            except (serial.SerialException, socket.error) as e:
                logger.error(f"Query error (attempt {attempt+1}): {e}")
            time.sleep(0.1)
        logger.error(f"Query failed after {retries} attempts: {desc}")
        return "Failed"

    def save_to_json(self):
        """Save current PV values to JSON file."""
        start_time = time.time()
        pv_values = {pv_name: pv.value for pv_name, pv in self.pvdb.items()}
        try:
            with open(self.json_file, 'w') as f:
                json.dump(pv_values, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            end_time = time.time()
            logger.debug(f"Saved PV values to {self.json_file} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
        except Exception as e:
            logger.error(f"Error saving JSON for AMP{self.port_index}: {e}")
            raise

    async def initialize_amplifier(self):
        """Reset the amplifier on startup."""
        self.send_command("*RST\r\n", "Resetting amplifier")
        await asyncio.sleep(LONG_SLEEP)

    # -------------------------------------------------------------------------
    # NAME PV (descriptor)
    # -------------------------------------------------------------------------
    @NAME.putter
    async def NAME(self, instance, value):
        """Set the amplifier name/descriptor."""
        # Handle bytes or string input
        if isinstance(value, bytes):
            str_value = value.decode('utf-8').rstrip('\x00')
        elif isinstance(value, (list, tuple)):
            # Convert character array to string
            str_value = ''.join(chr(c) if c != 0 else '' for c in value).rstrip('\x00')
        else:
            str_value = str(value)
        logger.debug(f"Setting NAME to '{str_value}' for AMP{self.port_index}")
        self.save_to_json()
        return str_value

    # -------------------------------------------------------------------------
    # SENSITIVITY PV
    # -------------------------------------------------------------------------
    @SENSITIVITY.putter
    async def SENSITIVITY(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting SENSITIVITY to {value} for AMP{self.port_index}")
        if self._updating_sensitivity:
            logger.debug(f"Blocked recursive update of SENSITIVITY for AMP{self.port_index}")
            return instance.value
        self._updating_sensitivity = True
        try:
            index = int(float(value))
            if index not in self.SENSITIVITY_MAP:
                raise ValueError(f"Sensitivity index {index} out of range (0-27)")
            self.send_command(f"SENS {index}\r\n", f"Setting sensitivity to index {index}")
            self.last_sens_index = index
            instance._data['value'] = float(index)
            self.GAIN._data['value'] = self.SENSITIVITY_MAP[index]
            await self.GAIN.publish(self.SENSITIVITY_MAP[index])
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"SENSITIVITY updated to {index} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(index)
        except Exception as e:
            logger.error(f"Error setting SENSITIVITY for AMP{self.port_index}: {e}")
            raise
        finally:
            self._updating_sensitivity = False

    @SENSITIVITY_CAL_MODE.putter
    async def SENSITIVITY_CAL_MODE(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting SENSITIVITY_CAL_MODE to {value} for AMP{self.port_index}")
        try:
            mode = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if mode not in (0, 1):
                raise ValueError(f"Sensitivity cal mode {mode} must be 0 (calibrated) or 1 (uncalibrated)")
            self.send_command(f"SUCM {mode}\r\n", f"Setting sensitivity cal mode to {mode}")
            instance._data['value'] = mode
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"SENSITIVITY_CAL_MODE updated to {mode} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return mode
        except Exception as e:
            logger.error(f"Error setting SENSITIVITY_CAL_MODE for AMP{self.port_index}: {e}")
            raise

    @SENSITIVITY_VERNIER.putter
    async def SENSITIVITY_VERNIER(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting SENSITIVITY_VERNIER to {value} for AMP{self.port_index}")
        try:
            vernier = int(float(value))
            if not 0 <= vernier <= 100:
                raise ValueError(f"Sensitivity vernier {vernier} must be between 0 and 100")
            self.send_command(f"SUCV {vernier}\r\n", f"Setting sensitivity vernier to {vernier}")
            instance._data['value'] = float(vernier)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"SENSITIVITY_VERNIER updated to {vernier} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(vernier)
        except Exception as e:
            logger.error(f"Error setting SENSITIVITY_VERNIER for AMP{self.port_index}: {e}")
            raise

    # -------------------------------------------------------------------------
    # INPUT OFFSET PVs
    # -------------------------------------------------------------------------
    @INPUT_OFFSET_ON.putter
    async def INPUT_OFFSET_ON(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting INPUT_OFFSET_ON to {value} for AMP{self.port_index}")
        try:
            state = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if state not in (0, 1):
                raise ValueError(f"Input offset on/off {state} must be 0 (off) or 1 (on)")
            self.send_command(f"IOON {state}\r\n", f"Setting input offset on/off to {state}")
            instance._data['value'] = state
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"INPUT_OFFSET_ON updated to {state} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return state
        except Exception as e:
            logger.error(f"Error setting INPUT_OFFSET_ON for AMP{self.port_index}: {e}")
            raise

    @INPUT_OFFSET_LEVEL.putter
    async def INPUT_OFFSET_LEVEL(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting INPUT_OFFSET_LEVEL to {value} for AMP{self.port_index}")
        if self._updating_offset:
            logger.debug(f"Blocked recursive update of INPUT_OFFSET_LEVEL for AMP{self.port_index}")
            return instance.value
        self._updating_offset = True
        try:
            level = int(float(value))
            if level not in self.OFFSET_LEVEL_MAP:
                raise ValueError(f"Input offset level index {level} must be between 0 and 29")
            self.send_command(f"IOLV {level}\r\n", f"Setting input offset level to {level}")
            instance._data['value'] = float(level)
            self.OFFSET_LEVEL_GAIN._data['value'] = self.OFFSET_LEVEL_MAP[level]
            await self.OFFSET_LEVEL_GAIN.publish(self.OFFSET_LEVEL_MAP[level])
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"INPUT_OFFSET_LEVEL updated to {level} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(level)
        except Exception as e:
            logger.error(f"Error setting INPUT_OFFSET_LEVEL for AMP{self.port_index}: {e}")
            raise
        finally:
            self._updating_offset = False

    @INPUT_OFFSET_SIGN.putter
    async def INPUT_OFFSET_SIGN(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting INPUT_OFFSET_SIGN to {value} for AMP{self.port_index}")
        try:
            sign = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if sign not in (0, 1):
                raise ValueError(f"Input offset sign {sign} must be 0 (negative) or 1 (positive)")
            self.send_command(f"IOSN {sign}\r\n", f"Setting input offset sign to {sign}")
            instance._data['value'] = sign
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"INPUT_OFFSET_SIGN updated to {sign} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return sign
        except Exception as e:
            logger.error(f"Error setting INPUT_OFFSET_SIGN for AMP{self.port_index}: {e}")
            raise

    @INPUT_OFFSET_CAL_MODE.putter
    async def INPUT_OFFSET_CAL_MODE(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting INPUT_OFFSET_CAL_MODE to {value} for AMP{self.port_index}")
        try:
            mode = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if mode not in (0, 1):
                raise ValueError(f"Input offset cal mode {mode} must be 0 (calibrated) or 1 (uncalibrated)")
            self.send_command(f"IOUC {mode}\r\n", f"Setting input offset cal mode to {mode}")
            instance._data['value'] = mode
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"INPUT_OFFSET_CAL_MODE updated to {mode} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return mode
        except Exception as e:
            logger.error(f"Error setting INPUT_OFFSET_CAL_MODE for AMP{self.port_index}: {e}")
            raise

    @INPUT_OFFSET_VERNIER.putter
    async def INPUT_OFFSET_VERNIER(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting INPUT_OFFSET_VERNIER to {value} for AMP{self.port_index}")
        try:
            vernier = int(float(value))
            if not -1000 <= vernier <= 1000:
                raise ValueError(f"Input offset vernier {vernier} must be between -1000 and 1000")
            self.send_command(f"IOUV {vernier}\r\n", f"Setting input offset vernier to {vernier}")
            instance._data['value'] = float(vernier)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"INPUT_OFFSET_VERNIER updated to {vernier} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(vernier)
        except Exception as e:
            logger.error(f"Error setting INPUT_OFFSET_VERNIER for AMP{self.port_index}: {e}")
            raise

    # -------------------------------------------------------------------------
    # BIAS VOLTAGE PVs
    # -------------------------------------------------------------------------
    @BIAS_VOLTAGE_ON.putter
    async def BIAS_VOLTAGE_ON(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting BIAS_VOLTAGE_ON to {value} for AMP{self.port_index}")
        try:
            state = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if state not in (0, 1):
                raise ValueError(f"Bias voltage on/off {state} must be 0 (off) or 1 (on)")
            self.send_command(f"BSON {state}\r\n", f"Setting bias voltage on/off to {state}")
            instance._data['value'] = state
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"BIAS_VOLTAGE_ON updated to {state} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return state
        except Exception as e:
            logger.error(f"Error setting BIAS_VOLTAGE_ON for AMP{self.port_index}: {e}")
            raise

    @BIAS_VOLTAGE_LEVEL.putter
    async def BIAS_VOLTAGE_LEVEL(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting BIAS_VOLTAGE_LEVEL to {value} for AMP{self.port_index}")
        try:
            level = int(float(value))
            if not -5000 <= level <= 5000:
                raise ValueError(f"Bias voltage level {level} must be between -5000 and 5000")
            self.send_command(f"BSLV {level}\r\n", f"Setting bias voltage level to {level}")
            instance._data['value'] = float(level)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"BIAS_VOLTAGE_LEVEL updated to {level} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(level)
        except Exception as e:
            logger.error(f"Error setting BIAS_VOLTAGE_LEVEL for AMP{self.port_index}: {e}")
            raise

    # -------------------------------------------------------------------------
    # FILTER PVs
    # -------------------------------------------------------------------------
    @FILTER_TYPE.putter
    async def FILTER_TYPE(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting FILTER_TYPE to {value} for AMP{self.port_index}")
        try:
            type_idx = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if type_idx not in self.FILTER_TYPE_MAP:
                raise ValueError(f"Filter type {type_idx} must be 0-5")
            self.send_command(f"FLTT {type_idx}\r\n", f"Setting filter type to {type_idx}")
            instance._data['value'] = type_idx
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"FILTER_TYPE updated to {type_idx} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return type_idx
        except Exception as e:
            logger.error(f"Error setting FILTER_TYPE for AMP{self.port_index}: {e}")
            raise

    @FILTER_LP_FREQ.putter
    async def FILTER_LP_FREQ(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting FILTER_LP_FREQ to {value} for AMP{self.port_index}")
        try:
            freq_idx = int(float(value))
            if freq_idx not in self.FILTER_LP_FREQ_MAP:
                raise ValueError(f"Lowpass filter frequency index {freq_idx} must be between 0 and 15")
            self.send_command(f"LFRQ {freq_idx}\r\n", f"Setting lowpass filter frequency to {freq_idx}")
            instance._data['value'] = float(freq_idx)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"FILTER_LP_FREQ updated to {freq_idx} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(freq_idx)
        except Exception as e:
            logger.error(f"Error setting FILTER_LP_FREQ for AMP{self.port_index}: {e}")
            raise

    @FILTER_HP_FREQ.putter
    async def FILTER_HP_FREQ(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting FILTER_HP_FREQ to {value} for AMP{self.port_index}")
        try:
            freq_idx = int(float(value))
            if freq_idx not in self.FILTER_HP_FREQ_MAP:
                raise ValueError(f"Highpass filter frequency index {freq_idx} must be between 0 and 11")
            self.send_command(f"HFRQ {freq_idx}\r\n", f"Setting highpass filter frequency to {freq_idx}")
            instance._data['value'] = float(freq_idx)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"FILTER_HP_FREQ updated to {freq_idx} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return float(freq_idx)
        except Exception as e:
            logger.error(f"Error setting FILTER_HP_FREQ for AMP{self.port_index}: {e}")
            raise

    @RESET_OVERLOAD.putter
    async def RESET_OVERLOAD(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting RESET_OVERLOAD to {value} for AMP{self.port_index}")
        try:
            trigger = int(float(value))
            if trigger not in (0, 1):
                raise ValueError(f"Reset overload trigger {trigger} must be 0 or 1")
            if trigger == 1:
                self.send_command("ROLD\r\n", "Resetting filter capacitors")
                logger.info(f"Filter capacitors reset for AMP{self.port_index}")
            instance._data['value'] = 0
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"RESET_OVERLOAD processed for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return 0
        except Exception as e:
            logger.error(f"Error setting RESET_OVERLOAD for AMP{self.port_index}: {e}")
            raise

    # -------------------------------------------------------------------------
    # GAIN MODE and OUTPUT PVs
    # -------------------------------------------------------------------------
    @GAIN_MODE.putter
    async def GAIN_MODE(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting GAIN_MODE to {value} for AMP{self.port_index}")
        try:
            mode = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if mode not in self.GAIN_MODE_MAP:
                raise ValueError(f"Gain mode {mode} must be 0-2")
            self.send_command(f"GNMD {mode}\r\n", f"Setting gain mode to {mode}")
            instance._data['value'] = mode
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"GAIN_MODE updated to {mode} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return mode
        except Exception as e:
            logger.error(f"Error setting GAIN_MODE for AMP{self.port_index}: {e}")
            raise

    @SIGNAL_INVERT.putter
    async def SIGNAL_INVERT(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting SIGNAL_INVERT to {value} for AMP{self.port_index}")
        try:
            state = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if state not in (0, 1):
                raise ValueError(f"Signal invert state {state} must be 0 or 1")
            self.send_command(f"INVT {state}\r\n", f"Setting signal invert to {state}")
            instance._data['value'] = state
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"SIGNAL_INVERT updated to {state} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return state
        except Exception as e:
            logger.error(f"Error setting SIGNAL_INVERT for AMP{self.port_index}: {e}")
            raise

    @BLANK_OUTPUT.putter
    async def BLANK_OUTPUT(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting BLANK_OUTPUT to {value} for AMP{self.port_index}")
        try:
            state = int(value) if isinstance(value, int) else instance.enum_strings.index(value)
            if state not in (0, 1):
                raise ValueError(f"Blank output state {state} must be 0 or 1")
            self.send_command(f"BLNK {state}\r\n", f"Setting blank output to {state}")
            instance._data['value'] = state
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"BLANK_OUTPUT updated to {state} for AMP{self.port_index}, "
                        f"took {end_time - start_time:.3f}s")
            return state
        except Exception as e:
            logger.error(f"Error setting BLANK_OUTPUT for AMP{self.port_index}: {e}")
            raise

    # -------------------------------------------------------------------------
    # GETTERS for computed PVs
    # -------------------------------------------------------------------------
    @OVERLOAD_STATUS.getter
    async def OVERLOAD_STATUS(self, instance):
        gain = self.SENSITIVITY_MAP.get(self.last_sens_index, 1e-12)
        return f"Last: {self.last_sens_index} (gain: {gain:.0e} V/A; check LED for overload)"

    @GAIN.getter
    async def GAIN(self, instance):
        index = int(self.SENSITIVITY.value)
        physical_value = self.SENSITIVITY_MAP.get(index, 1e-12)
        return physical_value

    @GAIN.putter
    async def GAIN(self, instance, value):
        start_time = time.time()
        logger.debug(f"Received WriteRequest for GAIN: value={value}, instance={instance}")
        if self._updating_sensitivity:
            logger.debug(f"Blocked recursive update of GAIN for AMP{self.port_index}")
            return instance.value
        self._updating_sensitivity = True
        try:
            target_value = float(value)
            closest_index = min(
                self.SENSITIVITY_MAP.keys(),
                key=lambda k: abs(self.SENSITIVITY_MAP[k] - target_value)
            )
            physical_value = self.SENSITIVITY_MAP[closest_index]
            logger.debug(f"Calculated index {closest_index} for value {target_value}")
            self.SENSITIVITY._data['value'] = float(closest_index)
            self.send_command(f"SENS {closest_index}\r\n", 
                            f"Setting sensitivity to index {closest_index}")
            instance._data['value'] = physical_value
            await instance.publish(physical_value)
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"GAIN updated to {physical_value} V/A (index {closest_index}) "
                        f"for AMP{self.port_index}, took {end_time - start_time:.3f}s")
            return physical_value
        except Exception as e:
            logger.error(f"Error setting GAIN for AMP{self.port_index}: {e}")
            raise
        finally:
            self._updating_sensitivity = False

    @OFFSET_LEVEL_GAIN.getter
    async def OFFSET_LEVEL_GAIN(self, instance):
        index = int(self.INPUT_OFFSET_LEVEL.value)
        physical_value = self.OFFSET_LEVEL_MAP.get(index, 1e-12)
        return physical_value

    @OFFSET_LEVEL_GAIN.putter
    async def OFFSET_LEVEL_GAIN(self, instance, value):
        start_time = time.time()
        logger.debug(f"Setting OFFSET_LEVEL_GAIN to {value} for AMP{self.port_index}")
        if self._updating_offset:
            logger.debug(f"Blocked recursive update of OFFSET_LEVEL_GAIN for AMP{self.port_index}")
            return instance.value
        self._updating_offset = True
        try:
            target_value = float(value)
            closest_index = min(
                self.OFFSET_LEVEL_MAP.keys(),
                key=lambda k: abs(self.OFFSET_LEVEL_MAP[k] - target_value)
            )
            physical_value = self.OFFSET_LEVEL_MAP[closest_index]
            self.INPUT_OFFSET_LEVEL._data['value'] = float(closest_index)
            await self.INPUT_OFFSET_LEVEL.publish(float(closest_index))
            self.send_command(f"IOLV {closest_index}\r\n", 
                            f"Setting input offset level to {closest_index}")
            instance._data['value'] = physical_value
            self.save_to_json()
            end_time = time.time()
            logger.debug(f"OFFSET_LEVEL_GAIN updated to {physical_value} A (index {closest_index}) "
                        f"for AMP{self.port_index}, took {end_time - start_time:.3f}s")
            return physical_value
        except Exception as e:
            logger.error(f"Error setting OFFSET_LEVEL_GAIN for AMP{self.port_index}: {e}")
            raise
        finally:
            self._updating_offset = False


def check_port(host: str, port: int) -> bool:
    """Check if a port is available on the given host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError as e:
            logger.error(f"Port {port} on {host} is not available: {e}")
            return False


async def startup(ioc: SRS570IOC, async_lib):
    """Startup hook to synchronize PVs with initial JSON values."""
    logger.debug(f"Starting PV synchronization with initial JSON values for AMP{ioc.port_index}...")
    if not ioc.initial_json_file.exists():
        logger.warning(f"Initial JSON file {ioc.initial_json_file} not found, using defaults")
        return

    try:
        with open(ioc.initial_json_file, 'r') as f:
            initial_values = json.load(f)

        for pv_name, value in initial_values.items():
            if pv_name in ioc.pvdb and not pv_name.endswith(":OVERLOAD_STATUS"):
                instance = ioc.pvdb[pv_name]
                await instance.write(value)
                logger.debug(f"Synced {pv_name} to {value} via putter")
                await instance.publish(value)

        # Handle OVERLOAD_STATUS separately (read-only style)
        prefix = ioc.port_config[ioc.port_index].get("prefix", f"SRS570_AMP{ioc.port_index}:")
        overload_key = f"{prefix}OVERLOAD_STATUS"
        if overload_key in initial_values:
            ioc.OVERLOAD_STATUS._data['value'] = initial_values[overload_key]
            logger.debug(f"Set OVERLOAD_STATUS to {initial_values[overload_key]}")
            await ioc.OVERLOAD_STATUS.publish(initial_values[overload_key])

        await asyncio.sleep(10.0)

        # Force INPUT_OFFSET_CAL_MODE to initial value
        cal_mode_key = f"{prefix}INPUT_OFFSET_CAL_MODE"
        if cal_mode_key in initial_values:
            logger.debug(f"Forcing INPUT_OFFSET_CAL_MODE to initial value "
                        f"{initial_values[cal_mode_key]} for AMP{ioc.port_index}")
            await ioc.INPUT_OFFSET_CAL_MODE.write(initial_values[cal_mode_key])
            await ioc.INPUT_OFFSET_CAL_MODE.publish(initial_values[cal_mode_key])

        for pv_name, pv in ioc.pvdb.items():
            logger.debug(f"Startup PV: {pv_name} = {pv.value}")

        logger.info(f"PV synchronization complete for AMP{ioc.port_index}")
    except Exception as e:
        logger.error(f"Error during startup synchronization for AMP{ioc.port_index}: {e}")
        raise


def run_ioc_process(port_index: int, ipaddr: str, port_config: dict, 
                    connection_mode: str, tcp_host: str = None):
    """Run a single IOC process for one amplifier."""
    global logger
    
    # Per-process logging setup
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, f'SRS570_AMP{port_index}.log')

    file_handler = TimedRotatingFileHandler(
        filename=log_file_path,
        when="midnight",
        interval=1,
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.WARNING)
    formatter = logging.Formatter(
        f'%(asctime)s - %(levelname)s - AMP{port_index} (PID:%(process)d) - %(message)s'
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger = logging.getLogger('caproto')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    logger.info(f"Logging initialized for AMP{port_index} to {log_file_path}")
    logger.info(f"Connection mode: {connection_mode}")

    try:
        ioc = SRS570IOC(
            port_index=port_index, 
            port_config=port_config,
            connection_mode=connection_mode,
            tcp_host=tcp_host
        )
        server_port = port_config[port_index]["server_port"]
        repeater_port = port_config[port_index]["repeater_port"]
        logger.info(f"Starting SRS570 IOC for amplifier {port_index} on server port {server_port}, "
                   f"repeater port {repeater_port}")

        host = ipaddr
        if not check_port(host, server_port) or not check_port(host, repeater_port):
            raise RuntimeError(f"Ports {server_port} or {repeater_port} are not available "
                             f"for AMP{port_index}")

        os.environ["EPICS_CA_SERVER_PORT"] = str(server_port)
        os.environ["EPICS_CA_REPEATER_PORT"] = str(repeater_port)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ioc.initialize_amplifier())

        run(
            ioc.pvdb,
            interfaces=[host],
            startup_hook=lambda async_lib: startup(ioc, async_lib),
            log_pv_names=True
        )
    except Exception as e:
        logger.error(f"Failed to start IOC for amplifier {port_index}: {e}")
        raise


# Global logger placeholder (will be set per-process)
logger = logging.getLogger('caproto')


if __name__ == "__main__":
    # Minimal global logging for main process
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - MAIN - %(message)s'
    )
    logger = logging.getLogger('caproto')
    logger.setLevel(logging.INFO)

    parser = argparse.ArgumentParser(
        description="Run SRS570 IOC for one or more amplifiers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with serial connection (default)
  python srs570_ioc.py -p 1,2 --mode serial

  # Run with TCP connection (MOXA)
  python srs570_ioc.py -p 1,2 --mode tcp --tcp-host 192.168.1.100

  # Run single amplifier in TCP mode
  python srs570_ioc.py -p 1 --mode tcp --tcp-host 10.0.0.50
        """
    )
    parser.add_argument(
        '-p', type=str, required=True,
        help="Port indices: comma-separated list (e.g., 1,2) from 1-4"
    )
    parser.add_argument(
        '--mode', type=str, choices=['serial', 'tcp'], default='serial',
        help="Connection mode: 'serial' for RS-232 or 'tcp' for MOXA TCP mode (default: serial)"
    )
    parser.add_argument(
        '--tcp-host', type=str, default=None,
        help="TCP host address for MOXA connection (required if mode is 'tcp')"
    )
    parser.add_argument(
        '--config', type=str, default=None,
        help="Path to custom configuration file (optional)"
    )
    args = parser.parse_args()

    # Get current hostname
    hostname = socket.gethostname()
    logger.info(f"Running on hostname: {hostname}")

    # Load server config
    server_config = ServerConfig.get_config(hostname, args.config)
    if server_config is None:
        raise ValueError(f"No config found for hostname '{hostname}'")
    
    ipaddr = server_config["ipaddr"]
    port_config_dict = server_config["PORT_CONFIG"]
    
    # Get TCP host from config or command line (command line overrides config)
    tcp_host = args.tcp_host or server_config.get("tcp_host")
    
    # Validate TCP arguments
    if args.mode == 'tcp' and not tcp_host:
        parser.error("--tcp-host is required when --mode is 'tcp' (or set 'tcp_host' in server_config.json)")
    
    logger.info(f"Loaded config for {hostname}: IP={ipaddr}")
    logger.info(f"Connection mode: {args.mode}")
    if args.mode == 'tcp':
        logger.info(f"MOXA TCP host: {tcp_host}")

    try:
        # Keep port_indices as strings since JSON keys are strings
        port_indices = [p.strip() for p in args.p.split(',')]
        for port_index in port_indices:
            if port_index not in port_config_dict:
                raise ValueError(f"Port index {port_index} must be in config for {hostname} "
                               f"(available: {list(port_config_dict.keys())})")
        if len(port_indices) != len(set(port_indices)):
            raise ValueError("Duplicate port indices are not allowed.")
    except ValueError as e:
        logger.error(f"Invalid port argument: {args.p}. {str(e)}")
        raise

    # Start processes
    processes = [
        Process(
            target=run_ioc_process, 
            args=(port_index, ipaddr, port_config_dict, args.mode, tcp_host)
        ) 
        for port_index in port_indices
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join()
