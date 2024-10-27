# Configuration F: 
# Frequency: 1.5GHz
# Voltage: 1.0V
# L1 Cache Size: 64KB
# L2 Cache Size: 1MB
# Memory Size: 1GB
# Number of Cores: Quad core

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
    def __init__(self, size='64kB', assoc=2):
        super(L1ICache, self).__init__()
        self.size = size
        self.assoc = assoc
        self.tag_latency = 2
        self.data_latency = 2
        self.response_latency = 2
        self.mshrs = 4
        self.tgts_per_mshr = 20

class L1DCache(Cache):
    def __init__(self, size='64kB', assoc=2):
        super(L1DCache, self).__init__()
        self.size = size
        self.assoc = assoc
        self.tag_latency = 2
        self.data_latency = 2
        self.response_latency = 2
        self.mshrs = 4
        self.tgts_per_mshr = 20

class L2Cache(Cache):
    def __init__(self, size='1MB', assoc=8):
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

num_cpu = 4

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


# 4. Process management for workload
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

# 5. Argument parser for simulation options
parser = argparse.ArgumentParser()
Options.addCommonOptions(parser)
Options.addSEOptions(parser)
if "--ruby" in sys.argv:
    Ruby.define_options(parser)
args = parser.parse_args()

# 6. Process management and workload setup
multiprocesses = []
numThreads = 1

if args.bench:
    apps = args.bench.split("-")
    if len(apps) != num_cpu:
        print("number of benchmarks not equal to set num_cpus!")
        sys.exit(1)
    for app in apps:
        try:
            if get_runtime_isa() == ISA.RISCV:
                exec(f"workload = {app}('riscv', 'linux', '{args.spec_input}')")
            else:
                exec(f"workload = {app}(buildEnv['TARGET_ISA'], 'linux', '{args.spec_input}')")
            multiprocesses.append(workload.makeProcess())
        except:
            print(f"Unable to find workload for {get_runtime_isa().name()}: {app}", file=sys.stderr)
            sys.exit(1)
elif args.cmd:
    multiprocesses, numThreads = get_processes(args)
else:
    print("No workload specified. Exiting!\n", file=sys.stderr)
    sys.exit(1)

(CPUClass, test_mem_mode, FutureClass) = Simulation.setCPUClass(args)
CPUClass.numThreads = numThreads

# 7. System Configuration (includes cache and memory settings)
system = System(
    cpu=[CPUClass(cpu_id=i) for i in range(num_cpu)],
    mem_mode='timing',  # Set memory mode to 'timing'
    mem_ranges=[AddrRange("1GB")],
    cache_line_size=args.cacheline_size
)

dvfs = DVFS(system)

if numThreads > 1:
    system.multi_thread = True

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
system.cpu = [RiscvTimingSimpleCPU() for i in range(num_cpu)]
for cpu in system.cpu:
    cpu.clk_domain = system.cpu_clk_domain
    cpu.createInterruptController()

# 11. Cache Configuration
for i in range(num_cpu):
    system.cpu[i].icache = L1ICache(size='64kB', assoc=2)
    system.cpu[i].dcache = L1DCache(size='64kB', assoc=2)
    system.cpu[i].icache_port = system.cpu[i].icache.cpu_side
    system.cpu[i].dcache_port = system.cpu[i].dcache.cpu_side

# 12. L2 Cache and Memory Bus Setup
system.l2cache = L2Cache(size='1MB', assoc=8)
system.l2bus = SystemXBar()

for i in range(num_cpu):
    system.cpu[i].icache.mem_side = system.l2bus.cpu_side_ports
    system.cpu[i].dcache.mem_side = system.l2bus.cpu_side_ports

system.l2cache.cpu_side = system.l2bus.mem_side_ports
system.l2cache.mem_side = system.membus.cpu_side_ports

system.system_port = system.membus.cpu_side_ports

# 13. Detailed Memory Configuration
system.mem_ctrl = SimpleMemory(range=system.mem_ranges[0])
system.mem_ctrl.port = system.membus.mem_side_ports

# 14. Workload and Simulation Setup
system.workload = SEWorkload.init_compatible(multiprocesses[0].executable)

for i in range(num_cpu):
    # Make sure there are enough processes in multiprocesses for each CPU
    if i < len(multiprocesses):
        system.cpu[i].workload = multiprocesses[i] if not args.smt else multiprocesses
    else:
        # If there aren't enough workload processes, assign the first one to avoid IndexError
        system.cpu[i].workload = multiprocesses[0] if not args.smt else multiprocesses
    system.cpu[i].createThreads()


# 15. Root Configuration
root = Root(full_system=False, system=system)
m5.instantiate()

# 16. Apply DVFS
dvfs.scale(voltage='1.0V', frequency='1.5GHz')

# 17. Simulation Experiment Design
# Define metrics for performance and power consumption
metrics = {
    'ipc': 0,  # Placeholder for Instructions per Cycle
    'energy_per_instruction': 0  # Placeholder for Energy per Instruction
}

# 18. New Custom Energy Calculations with Debugging
def calculate_power(voltage, frequency, capacitance_factor=1.0):
    """
    Calculates the dynamic power based on voltage, frequency, and a capacitance factor.
    Power (W) = C * V^2 * F
    """
    # Debug output for voltage and frequency
    print(f"Debug: Voltage = {voltage} V, Frequency = {frequency} Hz, Capacitance Factor = {capacitance_factor}")
    
    power = capacitance_factor * (voltage ** 2) * frequency
    return power

def calculate_memory_power(memory_usage_rate, base_power=0.5):
    """
    Estimates the memory power consumption based on the memory usage rate and base power.
    """
    return memory_usage_rate * base_power

# Get the current voltage and frequency from DVFS
cpu_voltage = dvfs.current_voltage
cpu_frequency = dvfs.current_frequency

if cpu_voltage is None or cpu_frequency is None:
    raise ValueError("Voltage and frequency have not been set by DVFS. Make sure to call dvfs.scale() before running the simulation.")

# Get the execution time in seconds
execution_time = m5.curTick() / 1e12  # Convert ticks to seconds

# Calculate dynamic power for the CPU
cpu_power = calculate_power(cpu_voltage, cpu_frequency)

# Debug output for the calculated power
print(f"Debug: Calculated CPU Power = {cpu_power} W")

# Assume some memory usage rate based on system activity (can be adjusted)
memory_usage_rate = 0.7  # Example rate, can be dynamically adjusted
memory_power = calculate_memory_power(memory_usage_rate)

# Total power is the sum of CPU power and memory power
total_power = cpu_power + memory_power

# Calculate the total energy consumption in different units
energy_joules = total_power * execution_time  # Energy in Joules
energy_microjoules = energy_joules * 1e6      # Energy in Microjoules (uJ)
energy_nanojoules = energy_joules * 1e9       # Energy in Nanojoules (nJ)
energy_picojoules = energy_joules * 1e12      # Energy in Picojoules (pJ)

# Print the results using plain ASCII text
print(f"CPU Power Consumption: {cpu_power:.4f} W")
print(f"Memory Power Consumption: {memory_power:.4f} W")
print(f"Total Power Consumption: {total_power:.4f} W")


# 19. Metric Tracking
m5.stats.reset()
m5.simulate()
m5.stats.dump()

# 20. Save stats.txt with a timestamp to avoid overwriting
m5out_dir = "m5out"
stats_file_path = os.path.join(m5out_dir, "stats.txt")
timestamp = time.strftime("%Y%m%d-%H%M%S")
new_stats_filename = os.path.join(m5out_dir, f"stats_{timestamp}.txt")

# Add "Hello world" to the stats file
if os.path.exists(stats_file_path):
    with open(stats_file_path, "a") as stats_file:
        stats_file.write("Configuration values\n")
        stats_file.write(f"Configuration Name: config_F \n")
        stats_file.write(f"Frequency: 1.5GHz\n")
        stats_file.write(f"Voltage: 1.0V \n")
        stats_file.write(f"L1 Cache Size: 64KB \n")
        stats_file.write(f"L2 Cache Size: 1MB\n")
        stats_file.write(f"Memory Size: 1GB\n")
        stats_file.write(f"Number of Cores: {num_cpu} \n")
        stats_file.write(f"CPU Power Consumption: {cpu_power:.8f} \n")
        stats_file.write(f"Memory Power Consumption: {memory_power:.8f} \n")
        stats_file.write(f"Total Power Consumption: {total_power:.8f} \n")

if os.path.exists(stats_file_path):
    os.rename(stats_file_path, new_stats_filename)
    print(f"Stats file saved as {new_stats_filename}")
else:
    print("stats.txt not found in m5out.")