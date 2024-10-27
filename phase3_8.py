import argparse
import sys
import os
import time
import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath, fatal, warn
from gem5.isas import ISA
from gem5.runtime import get_runtime_isa

# 1. Add to path for necessary imports
addToPath("../../")
from ruby import Ruby
from common import Options
from common import Simulation
from common import CacheConfig
from common import CpuConfig
from common import ObjectList
from common import MemConfig
from common.FileSystemConfig import config_filesystem
from common.Caches import *
from common.cpu2000 import *

# 2. Define basic L1 and L2 cache classes
class L1ICache(Cache):
    def __init__(self, size='8kB', assoc=2):
        super(L1ICache, self).__init__()
        self.size = size
        self.assoc = assoc
        self.tag_latency = 2
        self.data_latency = 2
        self.response_latency = 2
        self.mshrs = 4
        self.tgts_per_mshr = 20

class L1DCache(Cache):
    def __init__(self, size='8kB', assoc=2):
        super(L1DCache, self).__init__()
        self.size = size
        self.assoc = assoc
        self.tag_latency = 2
        self.data_latency = 2
        self.response_latency = 2
        self.mshrs = 4
        self.tgts_per_mshr = 20

class L2Cache(Cache):
    def __init__(self, size='256kB', assoc=8):
        super(L2Cache, self).__init__()
        self.size = size
        self.assoc = assoc
        self.tag_latency = 10
        self.data_latency = 10
        self.response_latency = 10
        self.mshrs = 20
        self.tgts_per_mshr = 12

# Function to convert frequency string to a float in Hz
def parse_frequency(frequency_str):
    """
    Converts a frequency string (e.g., '2GHz', '800MHz') to a float representing Hz.
    """
    if frequency_str.endswith('GHz'):
        return float(frequency_str.strip('GHz')) * 1e9
    elif frequency_str.endswith('MHz'):
        return float(frequency_str.strip('MHz')) * 1e6
    elif frequency_str.endswith('kHz'):
        return float(frequency_str.strip('kHz')) * 1e3
    elif frequency_str.endswith('Hz'):
        return float(frequency_str.strip('Hz'))
    else:
        raise ValueError(f"Unknown frequency format: {frequency_str}")

# 3. Dynamic Voltage and Frequency Scaling (DVFS) setup with Debugging
class DVFS:
    def __init__(self, system):
        self.system = system
        self.current_voltage = 0.0  # Initialize current_voltage with a default value
        self.current_frequency = 0.0  # Initialize current_frequency with a default value

    def scale(self, voltage, frequency):
        # Scale the frequency and voltage
        self.system.cpu_clk_domain.clock = frequency
        self.system.cpu_voltage_domain.voltage = str(voltage)  # Convert voltage to string
        
        # Update current voltage and frequency
        self.current_voltage = float(voltage.strip("V"))  # Store the current voltage as a float
        self.current_frequency = parse_frequency(frequency)  # Store the current frequency in Hz
        
        # Debug output to confirm scaling
        print(f"Debug: Scaling to Frequency = {self.current_frequency} Hz, Voltage = {self.current_voltage} V")

# 4. Logging configuration details using m5.stats.note()
def log_configurations(configuration_name, frequency, voltage, l1_cache_size, l2_cache_size, memory_size, num_cores):
    # Use m5.stats.note() to write configuration details to the stats file
    m5.stats.note(f"Configuration: {configuration_name}")
    m5.stats.note(f"Frequency: {frequency}")
    m5.stats.note(f"Voltage: {voltage}")
    m5.stats.note(f"L1 Cache Size: {l1_cache_size}")
    m5.stats.note(f"L2 Cache Size: {l2_cache_size}")
    m5.stats.note(f"Memory Size: {memory_size}")
    m5.stats.note(f"Number of Cores: {num_cores}")

# 5. Process management for workload
def get_processes(args):
    """Interprets provided args and returns a list of processes"""
    multiprocesses = []
    inputs = []
    outputs = []
    errouts = []
    pargs = []
    workloads = args.cmd.split(";")
    if args.input != "":
        inputs = args.input.split(";")
    if args.output != "":
        outputs = args.output.split(";")
    if args.errout != "":
        errouts = args.errout.split(";")
    if args.options != "":
        pargs = args.options.split(";")
    idx = 0
    for wrkld in workloads:
        process = Process(pid=100 + idx)
        process.executable = wrkld
        process.cwd = os.getcwd()
        process.gid = os.getgid()
        if args.env:
            with open(args.env, "r") as f:
                process.env = [line.rstrip() for line in f]
        if len(pargs) > idx:
            process.cmd = [wrkld] + pargs[idx].split()
        else:
            process.cmd = [wrkld]
        if len(inputs) > idx:
            process.input = inputs[idx]
        if len(outputs) > idx:
            process.output = outputs[idx]
        if len(errouts) > idx:
            process.errout = errouts[idx]
        multiprocesses.append(process)
        idx += 1
    if args.smt:
        assert args.cpu_type == "DerivO3CPU"
        return multiprocesses, idx
    else:
        return multiprocesses, 1

warn("The se.py script is deprecated. It will be removed in future releases of gem5.")

# 6. Argument parser for simulation options
parser = argparse.ArgumentParser()
Options.addCommonOptions(parser)
Options.addSEOptions(parser)
if "--ruby" in sys.argv:
    Ruby.define_options(parser)
args = parser.parse_args()

# 7. System Configuration (includes cache and memory settings)
system = System(
    mem_mode='timing',  # Set memory mode to 'timing'
    mem_ranges=[AddrRange(args.mem_size)],
    cache_line_size=args.cacheline_size
)

dvfs = DVFS(system)

# 8. Voltage and Clock Domain Configuration
system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
system.clk_domain = SrcClockDomain(
    clock=args.sys_clock, voltage_domain=system.voltage_domain
)
system.cpu_voltage_domain = VoltageDomain()
system.cpu_clk_domain = SrcClockDomain(
    clock=args.cpu_clock, voltage_domain=system.cpu_voltage_domain
)

# 9. Memory and Bus Configuration
system.membus = SystemXBar()

# 10. CPU Configuration
system.cpu = [RiscvTimingSimpleCPU() for i in range(args.num_cpus)]
for cpu in system.cpu:
    cpu.clk_domain = system.cpu_clk_domain
    cpu.createInterruptController()

# 11. Cache Configuration
for i in range(args.num_cpus):
    system.cpu[i].icache = L1ICache(size='8kB', assoc=2)
    system.cpu[i].dcache = L1DCache(size='8kB', assoc=2)
    system.cpu[i].icache_port = system.cpu[i].icache.cpu_side
    system.cpu[i].dcache_port = system.cpu[i].dcache.cpu_side

# 12. L2 Cache and Memory Bus Setup
system.l2cache = L2Cache(size='256kB', assoc=8)
system.l2bus = SystemXBar()

for i in range(args.num_cpus):
    system.cpu[i].icache.mem_side = system.l2bus.cpu_side_ports
    system.cpu[i].dcache.mem_side = system.l2bus.cpu_side_ports

system.l2cache.cpu_side = system.l2bus.mem_side_ports
system.l2cache.mem_side = system.membus.cpu_side_ports

system.system_port = system.membus.cpu_side_ports

# 13. Detailed Memory Configuration
system.mem_ctrl = SimpleMemory(range=system.mem_ranges[0])
system.mem_ctrl.port = system.membus.mem_side_ports

# 14. Workload and Simulation Setup
multiprocesses, numThreads = get_processes(args)
system.workload = SEWorkload.init_compatible(multiprocesses[0].executable)

for i in range(args.num_cpus):
    system.cpu[i].workload = multiprocesses[i]
    system.cpu[i].createThreads()

# 15. Root Configuration
root = Root(full_system=False, system=system)
m5.instantiate()

# 16. Apply DVFS
dvfs.scale(voltage='0.9V', frequency='800MHz')

# 17. Simulation Experiment Design
m5.stats.reset()

# Log configuration
log_configurations("Test Configuration", "800MHz", "0.9V", "8KB", "256KB", "512MB", "1 Core")

m5.simulate()
m5.stats.dump()

# 18. Power Calculations
def calculate_power(voltage, frequency, capacitance_factor=1.0):
    power = capacitance_factor * (voltage ** 2) * frequency
    return power

cpu_power = calculate_power(0.9, parse_frequency("800MHz"))
memory_usage_rate = 0.7
memory_power = calculate_power(0.5, 0.7 * parse_frequency("800MHz"))
total_power = cpu_power + memory_power
print(f"CPU Power Consumption: {cpu_power:.4f} W")
print(f"Memory Power Consumption: {memory_power:.4f} W")
print(f"Total Power Consumption: {total_power:.4f} W")

# 19. Save stats.txt
m5out_dir = "m5out"
timestamp = time.strftime("%Y%m%d-%H%M%S")
new_stats_filename = os.path.join(m5out_dir, f"stats_{timestamp}.txt")
stats_file_path = os.path.join(m5out_dir, "stats.txt")
if os.path.exists(stats_file_path):
    os.rename(stats_file_path, new_stats_filename)
    print(f"Stats file saved as {new_stats_filename}")
else:
    print("Stats file not found.")