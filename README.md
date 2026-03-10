# SRS570 IOC - EPICS IOC for Stanford Research Systems Model 570 Current Preamplifier

A Python-based EPICS IOC using caproto for controlling SRS570 current preamplifiers. Supports both RS-232 serial and TCP (MOXA) connection modes.

## Features

- **Dual Connection Modes**: Serial (RS-232) and TCP (MOXA) support
- **Multiple Amplifiers**: Control up to 4 amplifiers from a single instance
- **Full Parameter Control**: Sensitivity, offset, bias voltage, filters, gain mode
- **Docker Support**: Ready-to-use Docker configuration
- **JSON Configuration**: Flexible configuration via JSON files or environment variables
- **Automatic Logging**: Rotating log files per amplifier

## Available PVs

| PV Name | Type | Description |
|---------|------|-------------|
| `NAME` | STRING | Amplifier name/descriptor |
| `SENSITIVITY` | FLOAT | Sensitivity index (0-27) |
| `SENSITIVITY_CAL_MODE` | ENUM | Calibrated/Uncalibrated |
| `SENSITIVITY_VERNIER` | FLOAT | Uncalibrated vernier (0-100%) |
| `GAIN` | FLOAT | Sensitivity gain in V/A |
| `INPUT_OFFSET_ON` | ENUM | Offset on/off |
| `INPUT_OFFSET_LEVEL` | FLOAT | Offset level index (0-29) |
| `INPUT_OFFSET_SIGN` | ENUM | Negative/Positive |
| `INPUT_OFFSET_CAL_MODE` | ENUM | Calibrated/Uncalibrated |
| `INPUT_OFFSET_VERNIER` | FLOAT | Offset vernier (-1000 to 1000) |
| `OFFSET_LEVEL_GAIN` | FLOAT | Offset current in Amperes |
| `BIAS_VOLTAGE_ON` | ENUM | Bias on/off |
| `BIAS_VOLTAGE_LEVEL` | FLOAT | Bias level (-5000 to 5000 mV) |
| `FILTER_TYPE` | ENUM | Filter type (HP/LP/BP/None) |
| `FILTER_LP_FREQ` | FLOAT | Lowpass frequency index (0-15) |
| `FILTER_HP_FREQ` | FLOAT | Highpass frequency index (0-11) |
| `GAIN_MODE` | ENUM | Low Noise/High BW/Low Drift |
| `SIGNAL_INVERT` | ENUM | Non-inverted/Inverted |
| `BLANK_OUTPUT` | ENUM | No blank/Blank |
| `RESET_OVERLOAD` | FLOAT | Reset filter capacitors |
| `OVERLOAD_STATUS` | STRING | Status information |

## Installation

### Prerequisites

- Python 3.8+
- pip or conda
- Serial port access (for RS-232 mode) or network access (for TCP mode)

### Install Dependencies

```bash
pip install -r requirements.txt
```

### From Source

```bash
git clone https://github.com/your-org/srs570-ioc.git
cd srs570-ioc
pip install -r requirements.txt
```

## Configuration

### Server Configuration

Create or modify `config/server_config.json`:

```json
{
    "my-hostname": {
        "ipaddr": "192.168.1.50",
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
    }
}
```

### Initial Values

Create initial value files for each amplifier (e.g., `config/srs570_initial_amp1.json`):

```json
{
    "SRS570_AMP1:NAME": "My Amplifier",
    "SRS570_AMP1:SENSITIVITY": 15.0,
    "SRS570_AMP1:GAIN_MODE": 0,
    "SRS570_AMP1:FILTER_TYPE": 4
}
```

### Environment Variables

Alternatively, configure via environment variables:

```bash
export SRS570_IOC_IPADDR=0.0.0.0
export SRS570_AMP1_SERIAL=/dev/ttyUSB0
export SRS570_AMP1_TCP_PORT=4001
export SRS570_AMP1_SERVER_PORT=5064
export SRS570_AMP1_REPEATER_PORT=5065
export SRS570_AMP1_PREFIX=SRS570_AMP1:
```

## Running the IOC

### Serial Mode (RS-232)

Connect via RS-232 serial port:

```bash
# Single amplifier
python srs570_ioc.py -p 1 --mode serial

# Multiple amplifiers
python srs570_ioc.py -p 1,2,3,4 --mode serial
```

### TCP Mode (MOXA)

Connect via MOXA TCP-to-serial converter:

```bash
# Single amplifier
python srs570_ioc.py -p 1 --mode tcp --tcp-host 192.168.1.100

# Multiple amplifiers
python srs570_ioc.py -p 1,2 --mode tcp --tcp-host 192.168.1.100
```

### Using Custom Configuration

```bash
python srs570_ioc.py -p 1,2 --mode tcp --tcp-host 192.168.1.100 --config /path/to/config.json
```

## Docker Deployment

### Build the Image

```bash
docker build -t srs570-ioc .
```

### Run with Docker

```bash
# TCP mode
docker run -d \
  --name srs570-ioc \
  --network host \
  -e SRS570_CONNECTION_MODE=tcp \
  -e SRS570_TCP_HOST=192.168.1.100 \
  -e SRS570_PORTS=1,2 \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  srs570-ioc

# Serial mode (requires device access)
docker run -d \
  --name srs570-ioc \
  --network host \
  --device /dev/ttyUSB0:/dev/ttyUSB0 \
  --device /dev/ttyUSB1:/dev/ttyUSB1 \
  -e SRS570_CONNECTION_MODE=serial \
  -e SRS570_PORTS=1,2 \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  srs570-ioc
```

### Using Docker Compose

```bash
# Edit docker-compose.yml with your settings
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Docker Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SRS570_CONNECTION_MODE` | `tcp` | Connection mode: `serial` or `tcp` |
| `SRS570_TCP_HOST` | `192.168.1.100` | MOXA host address |
| `SRS570_PORTS` | `1` | Comma-separated amplifier indices |
| `SRS570_IOC_IPADDR` | `0.0.0.0` | IOC bind address |

## EPICS Client Examples

### Using caget/caput

```bash
# Get amplifier name
caget SRS570_AMP1:NAME

# Set sensitivity
caput SRS570_AMP1:SENSITIVITY 15

# Get gain value
caget SRS570_AMP1:GAIN

# Set gain mode
caput SRS570_AMP1:GAIN_MODE "Low Noise"

# Turn on bias voltage
caput SRS570_AMP1:BIAS_VOLTAGE_ON 1
caput SRS570_AMP1:BIAS_VOLTAGE_LEVEL 1000
```

### Using Python (pyepics)

```python
import epics

# Read values
name = epics.caget('SRS570_AMP1:NAME')
gain = epics.caget('SRS570_AMP1:GAIN')
sensitivity = epics.caget('SRS570_AMP1:SENSITIVITY')

# Write values
epics.caput('SRS570_AMP1:SENSITIVITY', 15)
epics.caput('SRS570_AMP1:NAME', 'My Detector Amp')

# Monitor changes
def callback(pvname, value, **kwargs):
    print(f"{pvname} = {value}")

epics.camonitor('SRS570_AMP1:GAIN', callback=callback)
```

## Troubleshooting

### Connection Issues

**Serial mode:**
- Check serial port permissions: `ls -la /dev/ttyUSB*`
- Add user to dialout group: `sudo usermod -a -G dialout $USER`
- Verify cable connections and SRS570 settings (9600 baud, 8N2)

**TCP mode:**
- Verify MOXA is reachable: `ping 192.168.1.100`
- Check MOXA port mapping (typically 4001, 4002, etc.)
- Verify firewall settings

### EPICS CA Issues

- Set EPICS environment: `export EPICS_CA_ADDR_LIST=<ioc-ip>`
- Check ports are not blocked: `netstat -tuln | grep 506`
- Verify network connectivity between client and IOC

### Viewing Logs

```bash
# View latest log
tail -f logs/SRS570_AMP1.log

# With Docker
docker logs -f srs570-ioc
```

## Project Structure

```
srs570-ioc/
├── srs570_ioc.py         # Main IOC implementation
├── config.py             # Configuration management
├── requirements.txt      # Python dependencies
├── Dockerfile            # Docker build file
├── docker-compose.yml    # Docker Compose configuration
├── docker-entrypoint.sh  # Docker entry point script
├── README.md             # This file
├── config/
│   ├── server_config.json        # Server configuration
│   └── srs570_initial_amp1.json  # Initial PV values
└── logs/                 # Log files (created at runtime)
```

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues and questions, please open a GitHub issue or contact the maintainers.
