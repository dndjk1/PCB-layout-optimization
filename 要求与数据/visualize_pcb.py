"""
PCB Placement Visualization Tool
Reads Bookshelf format (.nodes, .nets, .pl) and renders:
  - Component rectangles with labels
  - Pin positions on each component
  - Net connections between pins (colored by net)

Note: The Bookshelf converter skips GND/GROUND nets, so pins only
connected to ground will not appear in this visualization.
"""

import argparse
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D
from collections import defaultdict


def parse_nodes(filepath):
    nodes = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('UCLA') \
               or line.startswith('NumNodes') or line.startswith('NumTerminals'):
                continue
            parts = line.split()
            if len(parts) >= 3:
                name = parts[0]
                w, h = float(parts[1]), float(parts[2])
                is_terminal = len(parts) >= 4 and parts[-1].lower() == 'terminal'
                nodes[name] = {'width': w, 'height': h, 'terminal': is_terminal}
    return nodes


def parse_pl(filepath):
    placements = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('UCLA'):
                continue
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0]
                x, y = float(parts[1]), float(parts[2])
                orient = parts[3]
                fixed = '/FIXED' in line.upper()
                placements[name] = {'x': x, 'y': y, 'orient': orient, 'fixed': fixed}
    return placements


def parse_nets(filepath):
    nets = []
    current_net = None
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('UCLA') \
               or line.startswith('NumNets') or line.startswith('NumPins'):
                continue
            if line.startswith('NetDegree'):
                parts = line.split()
                degree = int(parts[2])
                net_name = parts[3]
                current_net = {'name': net_name, 'degree': degree, 'pins': []}
                nets.append(current_net)
            elif current_net is not None:
                parts = line.split()
                if len(parts) >= 5:
                    comp = parts[0]
                    dx, dy = float(parts[3]), float(parts[4])
                    current_net['pins'].append((comp, dx, dy))
    return nets


def visualize(benchmark_dir, output_path=None, show_pins=True, show_nets=True,
              max_net_degree=None, dpi=200, figsize=None):
    bench_name = os.path.basename(benchmark_dir.rstrip('/\\'))

    nodes = parse_nodes(os.path.join(benchmark_dir, f'{bench_name}.nodes'))
    placements = parse_pl(os.path.join(benchmark_dir, f'{bench_name}.pl'))
    nets = parse_nets(os.path.join(benchmark_dir, f'{bench_name}.nets'))

    # Canvas bounds
    all_x, all_y = [], []
    for name, pl in placements.items():
        if name in nodes:
            w, h = nodes[name]['width'], nodes[name]['height']
            all_x.extend([pl['x'], pl['x'] + w])
            all_y.extend([pl['y'], pl['y'] + h])

    min_x, max_x = min(all_x) - 5, max(all_x) + 5
    min_y, max_y = min(all_y) - 5, max(all_y) + 5

    if figsize is None:
        width_inches = 20
        height_inches = width_inches * (max_y - min_y) / (max_x - min_x)
        figsize = (width_inches, max(height_inches, 12))

    fig, ax = plt.subplots(1, 1, figsize=figsize, dpi=dpi)
    max_area = max(n['width'] * n['height'] for n in nodes.values())

    # --- Compute absolute pin positions ---
    # key = (comp, dx, dy) for physical pin identity
    # value = (abs_x, abs_y)
    physical_pins = {}
    net_pin_positions = {}  # (net_name, comp, pin_idx) -> (abs_x, abs_y)

    for net in nets:
        for i, (comp, dx, dy) in enumerate(net['pins']):
            if comp in placements and comp in nodes:
                pl = placements[comp]
                n = nodes[comp]
                abs_x = pl['x'] + n['width'] / 2 + dx
                abs_y = pl['y'] + n['height'] / 2 + dy
                physical_pins[(comp, dx, dy)] = (abs_x, abs_y)
                net_pin_positions[(net['name'], comp, i)] = (abs_x, abs_y)

    # --- Layer 1: Draw net connections (behind everything) ---
    if show_nets:
        cmap = matplotlib.colormaps.get_cmap('tab20').resampled(20)
        for idx, net in enumerate(nets):
            degree = net['degree']
            if max_net_degree is not None and degree > max_net_degree:
                continue

            coords = []
            for i, (comp, dx, dy) in enumerate(net['pins']):
                key = (net['name'], comp, i)
                if key in net_pin_positions:
                    coords.append(net_pin_positions[key])

            if len(coords) < 2:
                continue

            color = cmap(idx % 20)
            # Star topology: first pin connects to all others
            for j in range(1, len(coords)):
                ax.plot(
                    [coords[0][0], coords[j][0]],
                    [coords[0][1], coords[j][1]],
                    color=color, linewidth=0.8, alpha=0.6, zorder=1
                )

    # --- Layer 2: Draw components ---
    for name, pl in placements.items():
        if name not in nodes:
            continue
        w, h = nodes[name]['width'], nodes[name]['height']
        x, y = pl['x'], pl['y']
        area = w * h
        ratio = area / max_area if max_area > 0 else 0

        if ratio > 0.15:
            color = '#FFF8DC'   # cornsilk for large IC
            edgecolor = '#8B7355'
            linewidth = 2.0
        elif pl['fixed']:
            color = '#FFB3B3'
            edgecolor = '#CC0000'
            linewidth = 1.5
        else:
            color = '#B3D9FF'
            edgecolor = '#336699'
            linewidth = 1.2

        rect = patches.Rectangle(
            (x, y), w, h,
            linewidth=linewidth,
            edgecolor=edgecolor,
            facecolor=color,
            alpha=0.9,
            zorder=2
        )
        ax.add_patch(rect)

        fontsize = 5 if ratio < 0.05 else (7 if ratio < 0.15 else 8)
        ax.text(x + w / 2, y + h / 2, name,
                ha='center', va='center', fontsize=fontsize,
                fontweight='bold' if ratio > 0.15 else 'normal',
                color='#333333', zorder=3)

    # --- Layer 3: Draw ALL physical pins ---
    if show_pins:
        # Count how many nets each physical pin belongs to (for sizing)
        pin_net_count = defaultdict(int)
        for net in nets:
            if max_net_degree is not None and net['degree'] > max_net_degree:
                continue
            for comp, dx, dy in net['pins']:
                pin_net_count[(comp, dx, dy)] += 1

        for (comp, dx, dy), (px, py) in physical_pins.items():
            n_nets = pin_net_count.get((comp, dx, dy), 0)
            # Pins in more nets are drawn larger
            ms = 3.5 if n_nets > 2 else (2.5 if n_nets > 0 else 2.0)
            ax.plot(px, py, 'o', color='#222222', markersize=ms,
                    markeredgewidth=0, zorder=4)
            # White edge for contrast against components
            ax.plot(px, py, 'o', color='#222222', markersize=ms,
                    markerfacecolor='white', markeredgewidth=0.8,
                    markeredgecolor='#222222', zorder=4)

    total_pins = sum(n['degree'] for n in nets)
    multi_pin_nets = sum(1 for n in nets if n['degree'] >= 2)
    single_pin_nets = sum(1 for n in nets if n['degree'] == 1)

    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    ax.set_aspect('equal')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_title(
        f'PCB Placement Visualization — {bench_name}\n'
        f'{len(nodes)} components  |  {len(nets)} nets ({multi_pin_nets} multi-pin, '
        f'{single_pin_nets} single-pin)  |  {total_pins} pins  |  '
        f'{len(physical_pins)} unique physical pins'
    )
    ax.grid(True, alpha=0.1, linewidth=0.3)

    legend_elements = [
        patches.Patch(facecolor='#FFF8DC', edgecolor='#8B7355', label='Large IC (BGA)'),
        patches.Patch(facecolor='#FFB3B3', edgecolor='#CC0000', label='Fixed'),
        patches.Patch(facecolor='#B3D9FF', edgecolor='#336699', label='Movable'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='white',
               markeredgecolor='#222222', markersize=5, markeredgewidth=1,
               label='Pin'),
        Line2D([0], [0], color='#888888', linewidth=1, label='Net connection'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=7,
              framealpha=0.9, edgecolor='#cccccc')

    plt.tight_layout()

    if output_path is None:
        output_path = os.path.join(benchmark_dir, f'{bench_name}_visualization.png')

    fig.savefig(output_path, dpi=dpi, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f"Saved: {output_path}  ({len(nodes)} comps, {len(nets)} nets, "
          f"{total_pins} pins, {len(physical_pins)} unique)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Visualize PCB placement from Bookshelf format')
    parser.add_argument('benchmark_dir', nargs='?', default=None,
                        help='Path to benchmark directory (e.g., benchmarks/small-1)')
    parser.add_argument('-o', '--output', default=None, help='Output image path')
    parser.add_argument('--no-pins', action='store_true', help='Hide pin positions')
    parser.add_argument('--no-nets', action='store_true', help='Hide net connections')
    parser.add_argument('--max-net-degree', type=int, default=None,
                        help='Only draw nets with degree <= N')
    parser.add_argument('--dpi', type=int, default=200, help='Output DPI')
    parser.add_argument('--all', action='store_true', help='Visualize all 20 benchmarks')
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    cypress_dir = os.path.join(base_dir, 'Cypress')

    if args.all:
        for i in range(1, 21):
            bench_dir = os.path.join(cypress_dir, 'benchmarks', f'small-{i}')
            if os.path.isdir(bench_dir):
                visualize(bench_dir, show_pins=not args.no_pins,
                         show_nets=not args.no_nets,
                         max_net_degree=args.max_net_degree, dpi=args.dpi)
    elif args.benchmark_dir:
        visualize(args.benchmark_dir, output_path=args.output,
                 show_pins=not args.no_pins, show_nets=not args.no_nets,
                 max_net_degree=args.max_net_degree, dpi=args.dpi)
    else:
        default_dir = os.path.join(cypress_dir, 'benchmarks', 'small-1')
        if os.path.isdir(default_dir):
            visualize(default_dir, output_path=args.output,
                     show_pins=not args.no_pins, show_nets=not args.no_nets,
                     max_net_degree=args.max_net_degree, dpi=args.dpi)
        else:
            print("Usage: python visualize_pcb.py <benchmark_dir>")
            print("   or: python visualize_pcb.py --all")


if __name__ == '__main__':
    main()
