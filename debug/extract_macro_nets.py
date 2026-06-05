"""
Extract nets from the full microwatt DEF that connect to the 6 FIXED macros.
Outputs a minimal DEF fragment (NETS section only) for reimport.
"""
import sys, re

MACRO_INSTS = {
    "microwatt_0.soc0.bram.bram0.ram_0.memory_0",
    "microwatt_0.soc0.processor.dcache_0.rams:1.way.cache_ram_0",
    "microwatt_0.soc0.processor.execute1_0.multiply_0.multiplier",
    "microwatt_0.soc0.processor.icache_0.rams:1.way.cache_ram_0",
    "microwatt_0.soc0.processor.register_file_0.register_file_0",
    "microwatt_0.soc0.processor.with_fpu.fpu_0.fpu_multiply_0.multiplier",
}

def strip_escapes(s):
    return s.replace('\\[', '[').replace('\\]', ']')

def main():
    def_path = "/Users/ben/chip_designs/microwatt/user_project_wrapper.def.gz"
    import gzip
    
    in_nets = False
    current_net_lines = []
    current_net_name = None
    current_net_has_macro = False
    
    macro_nets = []  # (net_name, [pin_connections])
    
    print("Parsing...", file=sys.stderr)
    
    with gzip.open(def_path, 'rt', encoding='ascii', errors='replace') as f:
        for lineno, line in enumerate(f):
            line_s = line.rstrip()
            
            if line_s.startswith("NETS "):
                in_nets = True
                continue
            if line_s.startswith("END NETS"):
                # flush last net
                if current_net_has_macro and current_net_name:
                    macro_nets.append((current_net_name, current_net_lines[:]))
                in_nets = False
                break
            
            if not in_nets:
                continue
            
            # New net definition
            if line_s.startswith("- "):
                # Save previous net
                if current_net_has_macro and current_net_name:
                    macro_nets.append((current_net_name, current_net_lines[:]))
                # Start new net
                current_net_lines = [line_s]
                m = re.match(r'^- (\S+)', line_s)
                current_net_name = strip_escapes(m.group(1)) if m else None
                current_net_has_macro = False
            else:
                current_net_lines.append(line_s)
            
            # Check if this line references a macro instance
            # DEF pin format: ( inst_name pin_name )
            for inst in MACRO_INSTS:
                # Use escaped form too
                escaped = inst.replace('[', '\\[').replace(']', '\\]')
                if inst in line_s or escaped in line_s:
                    current_net_has_macro = True
                    break
            
            if lineno % 200000 == 0:
                print(f"  line {lineno//1000}k, macro nets so far: {len(macro_nets)}", file=sys.stderr)
    
    print(f"Found {len(macro_nets)} nets connecting to macro instances", file=sys.stderr)
    
    # Print summary: net name + which macros it touches
    for net_name, lines in macro_nets[:5]:
        print(f"  NET: {net_name}")
        for l in lines:
            if any(inst in l or inst.replace('[','\\[').replace(']','\\]') in l 
                   for inst in MACRO_INSTS):
                print(f"       {l.strip()}")
    
    # Write extracted nets to output file
    out_path = "/Users/ben/chip_designs/microwatt/macro_nets.txt"
    with open(out_path, 'w') as out:
        out.write(f"# {len(macro_nets)} nets connecting macro instances\n")
        for net_name, lines in macro_nets:
            # Collect which macros this net touches
            touched = set()
            for inst in MACRO_INSTS:
                escaped = inst.replace('[', '\\[').replace(']', '\\]')
                for l in lines:
                    if inst in l or escaped in l:
                        touched.add(inst.split('.')[-1])  # short name
                        break
            out.write(f"{net_name} : {', '.join(sorted(touched))}\n")
    
    print(f"Wrote net summary to {out_path}", file=sys.stderr)

main()
