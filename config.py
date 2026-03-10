"""
Configuration module for SRS570 IOC.

This module provides server and port configuration for the SRS570 IOC.
Configuration can be loaded from environment variables, JSON files, or
use default values.
"""

import os
import json
import socket
from pathlib import Path
from typing import Dict, Optional, Any


class ServerConfig:
    """
    Server configuration manager for SRS570 IOC.
    
    Supports configuration via:
    - Environment variables
    - JSON configuration files
    - Default hardcoded values
    """
    
    # Default server configurations
    DEFAULT_CONFIGS = {
        "default": {
            "ipaddr": "0.0.0.0",
            "PORT_CONFIG": {
                1: {
                    "serial": "/dev/ttyUSB0",
                    "tcp_port": 4001,
                    "server_port": 5064,
                    "repeater_port": 5065,
                    "prefix": "SRS570_AMP1:"
                },
                2: {
                    "serial": "/dev/ttyUSB1",
                    "tcp_port": 4002,
                    "server_port": 5066,
                    "repeater_port": 5067,
                    "prefix": "SRS570_AMP2:"
                },
                3: {
                    "serial": "/dev/ttyUSB2",
                    "tcp_port": 4003,
                    "server_port": 5068,
                    "repeater_port": 5069,
                    "prefix": "SRS570_AMP3:"
                },
                4: {
                    "serial": "/dev/ttyUSB3",
                    "tcp_port": 4004,
                    "server_port": 5070,
                    "repeater_port": 5071,
                    "prefix": "SRS570_AMP4:"
                }
            }
        }
    }
    
    @classmethod
    def get_config(cls, hostname: str = None, config_file: str = None) -> Dict[str, Any]:
        """
        Get server configuration.
        
        Priority:
        1. JSON config file (if provided)
        2. Environment variables
        3. Hostname-based defaults
        4. Generic defaults
        
        Args:
            hostname: Server hostname (defaults to current hostname)
            config_file: Path to JSON configuration file
            
        Returns:
            Configuration dictionary
        """
        if hostname is None:
            hostname = socket.gethostname()
        
        # Try loading from config file first
        if config_file:
            config = cls._load_from_file(config_file)
            if config:
                return config.get(hostname, config.get("default", cls.DEFAULT_CONFIGS["default"]))
        
        # Try loading from environment variables
        config = cls._load_from_env()
        if config:
            return config
        
        # Try loading from default config directory
        default_config_path = Path("config/server_config.json")
        if default_config_path.exists():
            config = cls._load_from_file(str(default_config_path))
            if config:
                return config.get(hostname, config.get("default", cls.DEFAULT_CONFIGS["default"]))
        
        # Fall back to defaults
        return cls.DEFAULT_CONFIGS.get(hostname, cls.DEFAULT_CONFIGS["default"])
    
    @classmethod
    def _load_from_file(cls, filepath: str) -> Optional[Dict[str, Any]]:
        """Load configuration from a JSON file."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load config from {filepath}: {e}")
            return None
    
    @classmethod
    def _load_from_env(cls) -> Optional[Dict[str, Any]]:
        """Load configuration from environment variables."""
        ipaddr = os.environ.get("SRS570_IOC_IPADDR")
        if not ipaddr:
            return None
        
        # Build port config from environment variables
        port_config = {}
        for i in range(1, 5):
            prefix = f"SRS570_AMP{i}_"
            serial = os.environ.get(f"{prefix}SERIAL", f"/dev/ttyUSB{i-1}")
            tcp_port = int(os.environ.get(f"{prefix}TCP_PORT", 4000 + i))
            server_port = int(os.environ.get(f"{prefix}SERVER_PORT", 5062 + (i - 1) * 2))
            repeater_port = int(os.environ.get(f"{prefix}REPEATER_PORT", 5063 + (i - 1) * 2))
            pv_prefix = os.environ.get(f"{prefix}PREFIX", f"SRS570_AMP{i}:")
            
            port_config[i] = {
                "serial": serial,
                "tcp_port": tcp_port,
                "server_port": server_port,
                "repeater_port": repeater_port,
                "prefix": pv_prefix
            }
        
        return {
            "ipaddr": ipaddr,
            "PORT_CONFIG": port_config
        }
    
    @classmethod
    def generate_sample_config(cls, filepath: str = "config/server_config.sample.json"):
        """Generate a sample configuration file."""
        sample_config = {
            "my-server-hostname": {
                "ipaddr": "192.168.1.100",
                "PORT_CONFIG": {
                    "1": {
                        "serial": "/dev/ttyUSB0",
                        "tcp_port": 4001,
                        "server_port": 5064,
                        "repeater_port": 5065,
                        "prefix": "BL01:SRS570_AMP1:"
                    },
                    "2": {
                        "serial": "/dev/ttyUSB1",
                        "tcp_port": 4002,
                        "server_port": 5066,
                        "repeater_port": 5067,
                        "prefix": "BL01:SRS570_AMP2:"
                    }
                }
            },
            "default": cls.DEFAULT_CONFIGS["default"]
        }
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(sample_config, f, indent=2)
        print(f"Sample configuration written to {filepath}")


# Backward compatibility: ServerList dict
ServerList = ServerConfig.DEFAULT_CONFIGS
