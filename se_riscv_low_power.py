import argparse
import sys
import os
import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath, fatal, warn
from gem5.isas import ISA
from gem5.runtime import get_runtime_isa
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

# Define basic L1 and L2 cache classes
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

class DVFS:
    def __init__(self, system):
        self.system = system

    def scale(self, voltage, frequency):
        # Scale the frequency and voltage
        self.system.cpu_clk_domain.clock = frequency
        self.system.cpu_voltage_domain.voltage = voltage
        print(f"DVFS: Scaling to {frequency} and voltage {voltage}V")

def apply_clock_gating(cpu):
    # Apply clock gating by reducing the clock speed when the CPU is idle
    if cpu.is_idle:
        cpu.clk_domain.clock = '100MHz'
    else:
        cpu.clk_domain.clock = '1GHz'
    print(f"Clock Gating: {'Enabled' if cpu.is_idle else 'Disabled'} for CPU {cpu.cpu_id}")

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

parser = argparse.ArgumentParser()
Options.addCommonOptions(parser)
Options.addSEOptions(parser)
if "--ruby" in sys.argv:
    Ruby.define_options(parser)
args = parser.parse_args()

multiprocesses = []
numThreads = 1

if args.bench:
    apps = args.bench.split("-")
    if len(apps) != args.num_cpus:
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

# Check -- do not allow SMT with multiple CPUs
if args.smt and args.num_cpus > 1:
    fatal("You cannot use SMT with multiple CPUs!")

np = args.num_cpus
mp0_path = multiprocesses[0].executable

# System Configuration
system = System(
    cpu=[CPUClass(cpu_id=i) for i in range(np)],
    mem_mode='timing',  # Set memory mode to 'timing'
    mem_ranges=[AddrRange(args.mem_size)],
    cache_line_size=args.cacheline_size
)

dvfs = DVFS(system)

if numThreads > 1:
    system.multi_thread = True

# Create a top-level voltage domain
system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)

# Create a source clock for the system and set the clock period
system.clk_domain = SrcClockDomain(
    clock=args.sys_clock, voltage_domain=system.voltage_domain
)

# Create a CPU voltage domain
system.cpu_voltage_domain = VoltageDomain()

# Create a separate clock domain for the CPUs
system.cpu_clk_domain = SrcClockDomain(
    clock=args.cpu_clock, voltage_domain=system.cpu_voltage_domain
)

# Set up the memory bus first
system.membus = SystemXBar()

# CPU configuration
system.cpu = [RiscvTimingSimpleCPU() for i in range(np)]

# All CPUs belong to a common cpu_clk_domain, therefore running at a common frequency
for cpu in system.cpu:
    cpu.clk_domain = system.cpu_clk_domain
    cpu.createInterruptController()

# Cache hierarchy
for i in range(np):
    system.cpu[i].icache = L1ICache(size='8kB', assoc=2)
    system.cpu[i].dcache = L1DCache(size='8kB', assoc=2)
    # Connect L1 caches to the CPU
    system.cpu[i].icache_port = system.cpu[i].icache.cpu_side
    system.cpu[i].dcache_port = system.cpu[i].dcache.cpu_side

# Add L2 cache
system.l2cache = L2Cache(size='256kB', assoc=8)
system.l2bus = SystemXBar()

for i in range(np):
    system.cpu[i].icache.mem_side = system.l2bus.cpu_side_ports
    system.cpu[i].dcache.mem_side = system.l2bus.cpu_side_ports

system.l2cache.cpu_side = system.l2bus.mem_side_ports
system.l2cache.mem_side = system.membus.cpu_side_ports

# Connect system port to the memory bus
system.system_port = system.membus.cpu_side_ports

# Detailed memory configuration using SimpleMemory
system.mem_ctrl = SimpleMemory(range=system.mem_ranges[0])
system.mem_ctrl.port = system.membus.mem_side_ports

# Workload and simulation setup
system.workload = SEWorkload.init_compatible(mp0_path)

for i in range(np):
    system.cpu[i].workload = multiprocesses[i] if not args.smt else multiprocesses
    system.cpu[i].createThreads()

# Apply DVFS during the simulation
dvfs.scale(voltage=0.9, frequency='800MHz')

if args.wait_gdb:
    system.workload.wait_for_remote_gdb = True

root = Root(full_system=False, system=system)
Simulation.run(args, root, system, FutureClass)

