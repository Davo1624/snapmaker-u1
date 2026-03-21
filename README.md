With U1 extended firmware offering the ability to use nfc tags with the Openspool protocol, I wanted to find a way to automatically track filament usage without having to manually set spools or scan QR codes.

This setup works by introducing a simple mapping layer between toolheads and spool IDs, then using that mapping to control Spoolman automatically.

Each toolhead is associated with a fixed channel (e.g., extruder → channel 0, extruder1 → channel 1, etc.). Instead of attaching spool data directly to the toolhead or UI, each channel stores a spool ID in a persistent variable.

When a spool is assigned to a channel (either manually or via NFC), the system:
updates an in-memory state (SPOOL_STATE)
saves the value to disk using SAVE_VARIABLE
This ensures the mapping persists across restarts.

Tool changes (T0–T3) are wrapped with macros so that whenever a tool is selected:
the current extruder is mapped to its channel
the channel’s stored spool ID is retrieved
Spoolman is updated with that spool ID via a Moonraker API call

As a result, the active spool always follows the selected tool automatically.

On startup, a delayed macro restores all saved channel-to-spool assignments back into memory. Optionally, a scan process (e.g., NFC) can update these assignments if a new spool is detected.

Log Processing Flow
Klipper logs all macro execution and state changes to klippy.log. Moonraker monitors Klipper in real time via its API layer, exposing printer state and handling remote method calls (such as spoolman_set_active_spool). When macros trigger these calls, Moonraker forwards the updated spool information to Spoolman, which tracks usage and metadata. The UI (e.g., Fluidd) reads state from Moonraker, but in this setup it is not required for spool logic—data flows from Klipper → Moonraker → Spoolman, with klippy.log serving as the authoritative record for debugging and traceability.

See below for requirements/notes/limitations:

- You need to running extended firmware (https://github.com/paxx12/SnapmakerU1-Extended-Firmware)
- This how-to assumes you have already installed spoolman (https://github.com/Donkie/Spoolman) and are writing your own nfc tags (I use spoolpainter https://play.google.com/store/apps/details?id=com.spoolpainter.app)
- An easy way to sync spoolman filament profiles to orca/snorca is baze's great import tool (https://gitlab.com/baze/spoolman-orca-filament-profile-generator), not necessary but very nice indeed
- This  only works with OpenSpool protocol. You need to physically remove any OEM nfc tags
- Right now the script needs to be run manually after each reboot, looking into how it can be invoked on boot
- The python script and gcode blocks were coded with AI so if there are any mistakes, blame ChatGPT

And here are the various steps:

1. Save the `01_spoolman_klipper.cfg` file to `/home/lava/printer_data/config/extended/klipper`. This is the main custom gcode block that tracks toolhead changes.
2. Restart klipper service from fluidd ui System tab.
3. Save the `nfc_spool_reader.py` file to `/home/lava/printer_data/config/extended`. This is the python script that syncs the active toolhead (and thus filament usage) to spoolman.
4. Run the command `chmod +x /home/lava/printer_data/config/extended/nfc_spool_reader.py` to make the script executable.
5. Run the command `touch /home/lava/printer_data/extended/variables.cfg && chown lava:lava /home/lava/printer_data/extended/variables.cfg`. This command will create a file and assign user/group as lava:lava, this file is used to store spool data and allow it to persist between reboots.
6. Save the `05_spoolman.cfg` file to `/home/lava/printer_data/config/extended/moonraker` and be sure to edit the spoolman url to match your setup. This tells moonraker where the spoolamn service is located.
7. Modify the machine gcode to prevent it from changing toolhead assignments outside of the script.
   - Click the pencil icon next to the printer name <img src="https://uploads.namegoeshere.net/u/I9shpf.png">
   - Click the `Machine G-code` tab and then inside the `Machine start G-code` block scroll down to the second block of text.
   - Below the `T{initial_extruder}` line add `START_SPOOLMAN_TRACKING`, this will invoke the `START_SPOOLMAN_TRACKING` gcode macro from the custom gcode block we created in Step 1 when the first extruder is intiated for a print. <img src="https://uploads.namegoeshere.net/u/IVaxYF.png">
   - Scroll down to the `Change filament G-code` block and look for a line that says `"USE_CHANNEL CHANNEL=" + next_extruder + "`
   - Delete the `"USE_CHANNEL CHANNEL=" + next_extruder + "` line and the line immediately below it (two lines highlighted in the screenshot). <img serc="https://uploads.namegoeshere.net/u/F76xMC.png">
8. Save the new machine profile. NOTE: you will have to use this profile for the script to work.
9. Save the `start_nfc_spool.sh` file to `/home/lava/printer_data/config/extended`
10. Run the command `chmod +x /home/lava/printer_data/config/extended/start_nfc_spool.sh` to make the script executable.
11. To start the script run the command `sh /home/lava/printer_data/config/extended/start_nfc_spool.sh &`
12. Check the log file to confirm the script is running: `tail -f /home/lava/printer_data/logs/nfc_spool_reader.log`
13. In fluidd ui click the `Console` tab and run the command `START_SPOOLMAN_TRACKING` to clear any existing spools and read the nfc tags of any loaded spools followed by `SHOW_SPOOL_STATE` to confirm which spools are conisdered "loaded" by the script.
