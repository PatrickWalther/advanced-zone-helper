# Advanced Zone Helper

A KiCad plugin that creates zones from selected shapes, including ring zones and multi-hole zones between nested outlines.

## Demo

https://github.com/user-attachments/assets/ab84076c-92c4-4066-be21-093f74fc34f7

## Features

- Automatically detects closed loops from selected lines, arcs, and circles
- Creates ring zones between nested outlines (e.g., between two concentric circles)
- Supports multi-hole zones (outer boundary with multiple inner cutouts)
- Configure layer, net, priority, and clearance settings
- Preview detected zones before creation

## Installation

### Option 1: KiCad Plugin Manager (Recommended)

1. Download the latest `advanced-zone-helper-vX.X.X-pcm.zip` from [Releases](../../releases)
2. In KiCad, go to **Plugin and Content Manager**
3. Click **Install from File...** and select the downloaded ZIP

### Option 2: Manual Installation

1. Download the latest `advanced-zone-helper-vX.X.X.zip` from [Releases](../../releases)
2. Extract to your KiCad plugins directory:
   - Windows: `%USERPROFILE%\Documents\KiCad\8.0\scripting\plugins\`
   - Linux: `~/.local/share/kicad/8.0/scripting/plugins/`
   - macOS: `~/Library/Preferences/kicad/8.0/scripting/plugins/`
3. Restart KiCad

## Troubleshooting

- **Plugin not appearing**: Check the plugin is in the correct directory and restart KiCad
- **No shapes found**: Ensure you selected graphic shapes (not footprints/tracks)
- **No closed loops found**: Endpoints must connect within 0.001mm tolerance

## Requirements

- KiCad 7.0 or later

No additional Python packages required - the plugin uses only KiCad's built-in Python environment.

## License

MIT License
