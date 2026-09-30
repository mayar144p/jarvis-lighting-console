"""Jarvis self-test - run this first after changing anything.

    python tools/selftest.py

Checks: GDTF parser, database import/search, the layout generator,
Art-Net packet bytes, channel roles and the console engine
(patch, programmer, merges, cues, fades, show files, gates) -
plus the M6 suites: sACN packets, 16-bit DMX assembly, DMX input,
MIDI mapping, auto-follow, fixture profiles and simulated scan.

Every suite runs isolated: one exception is reported as a failure and the
rest of the run continues, so a single broken test can never hide the
other 600 checks behind it.
"""
from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.selftests import common  # noqa: E402
from tools.selftests.common import check  # noqa: E402
from tools.selftests.part01 import (  # noqa: E402
    test_artnet,
    test_artnet_discovery,
    test_autopatch,
    test_channel_roles,
    test_console_ai,
    test_db,
    test_engine,
    test_fx_autosave,
    test_gdtf,
    test_gdtf_spec,
    test_sacn,
    test_showdesign,
)
from tools.selftests.part02 import (  # noqa: E402
    test_aim,
    test_autofollow,
    test_channels,
    test_channels_16bit,
    test_dmx16,
    test_dmx_input,
    test_midi,
    test_palette_targets,
    test_profiles,
    test_scan_simulated,
)
from tools.selftests.part03 import (  # noqa: E402
    test_arrange,
    test_command_line,
    test_dry_run_button,
    test_fixture_editor,
    test_limits_and_lock,
    test_network_address,
    test_physical_ranges,
)
from tools.selftests.part04 import (  # noqa: E402
    test_attribute_grid,
    test_client_contracts,
    test_colour_picker,
    test_console_only,
    test_cue_editing,
    test_fan,
    test_selection_tools,
    test_undo,
    test_ux_contracts,
)
from tools.selftests.part05 import (  # noqa: E402
    test_api_auth,
    test_fixture_kind,
    test_fx_library,
    test_gdtf_geometry,
    test_gdtf_share,
    test_hardening,
    test_web_app,
)
from tools.selftests.part06 import (  # noqa: E402
    test_auto_update,
    test_autoshow,
    test_button_fades,
    test_custom_buttons,
    test_dmx_target,
    test_motion,
    test_open_libraries,
    test_quick_buttons,
    test_remember_open,
    test_shutter_rest,
    test_timeline,
    test_venue,
    test_visual_motion,
    test_wheel_slots,
)
from tools.selftests.part07 import (  # noqa: E402
    test_aim_debug,
    test_arm_for_set,
    test_aux_channels,
    test_big_rig_groups,
    test_button_midi,
    test_button_speed,
    test_button_tiles,
    test_change_type,
    test_co2_preset,
    test_cue_fx_parts,
    test_cue_list_modes,
    test_dmx_clashes,
    test_group_flash_laser_button,
    test_look_types,
    test_looks,
    test_more_models,
    test_multi_head,
    test_my_moves,
    test_my_venues,
    test_rdm,
    test_ready_versions,
    test_review_fixes,
    test_rig_tools,
    test_room_fit,
    test_steady_dmx,
    test_tablets,
    test_virtual_dimmer,
)
from tools.selftests.part08 import (  # noqa: E402
    check_js,
    test_beam_bar,
    test_discovery,
    test_engine_api,
    test_fixture_search,
    test_fx_safety,
    test_light_test,
    test_manual_fixture,
    test_merge,
    test_realtime,
    test_share_relogin,
    test_show_building,
)
from tools.selftests.part09 import (  # noqa: E402
    test_autopilot,
    test_beat_clock,
    test_midi_monitor,
    test_room_making,
    test_sound_analysis,
    test_sound_reactive,
    test_spatial_fx,
    test_virtual_node,
)


def _suites():
    return (
    ("gdtf parser", test_gdtf),
    ("gdtf spec layout", test_gdtf_spec),
    ("database", test_db),
    ("artnet packets", test_artnet),
    ("artnet discovery", test_artnet_discovery),
    ("rig discovery", test_discovery),
    ("moving-head aim", test_aim),
    ("gdtf share", test_gdtf_share),
    ("palette targeting", test_palette_targets),
    ("dmx channel sheet", test_channels),
    ("16-bit channel sheet", test_channels_16bit),
    ("undo / redo", test_undo),
    ("selection tools", test_selection_tools),
    ("cue-list editing", test_cue_editing),
    ("fanning", test_fan),
    ("attribute grid", test_attribute_grid),
    ("arrange", test_arrange),
    ("network address", test_network_address),
    ("limits + lock", test_limits_and_lock),
    ("command line", test_command_line),
    ("dry run button", test_dry_run_button),
    ("physical ranges", test_physical_ranges),
    ("fixture editor", test_fixture_editor),
    ("channel roles", test_channel_roles),
    ("console engine", test_engine),
    ("auto patch", test_autopatch),
    ("fx + autosave", test_fx_autosave),
    ("AI console compiler", test_console_ai),
    ("sacn transport", test_sacn),
    ("16-bit dmx", test_dmx16),
    ("dmx input", test_dmx_input),
    ("midi", test_midi),
    ("auto-follow", test_autofollow),
    ("fixture profiles", test_profiles),
    ("realtime budget", test_realtime),
    ("merge core", test_merge),
    ("console api", test_engine_api),
    ("hardening", test_hardening),
    ("fixture kind", test_fixture_kind),
    ("feed contracts", test_ux_contracts),
    ("client contracts", test_client_contracts),
    )


def _standalone_suites():
    return (
    ("simulated scan", test_scan_simulated),
    ("colour on the wire", test_colour_picker),
    ("single app", test_console_only),
    ("web app", test_web_app),
    ("show design", test_showdesign),
    ("api auth", test_api_auth),
    ("gdtf geometry", test_gdtf_geometry),
    ("fx library", test_fx_library),
    ("show building", test_show_building),
    ("venue", test_venue),
    ("quick buttons", test_quick_buttons),
    ("timeline", test_timeline),
    ("auto show", test_autoshow),
    ("dmx target", test_dmx_target),
    ("shutter rests open", test_shutter_rest),
    ("colour wheel slots", test_wheel_slots),
    ("update on launch", test_auto_update),
    ("open fixture libraries", test_open_libraries),
    ("visual matches the rig", test_visual_motion),
    ("lasers and special effects", test_fx_safety),
    ("fixture from its manual", test_manual_fixture),
    ("gdtf share session expiry", test_share_relogin),
    ("shutter open value found and remembered", test_remember_open),
    ("test this light", test_light_test),
    ("forgiving fixture search", test_fixture_search),
    ("beam bar lasers", test_beam_bar),
    ("every channel gets a control", test_aux_channels),
    ("CO2 preset mode never fires on its own", test_co2_preset),
    ("movement stays where it is aimed", test_motion),
    ("buttons as customisable as possible", test_custom_buttons),
    ("ARM for the whole set; lasers stay on", test_arm_for_set),
    ("My moves: named movements, not cues", test_my_moves),
    ("Looks: named, one tap brings it all back", test_looks),
    ("grouping for big rigs", test_big_rig_groups),
    ("My venues: saved per venue, any room shape, poles", test_my_venues),
    ("DMX map: every address clash found and fixable", test_dmx_clashes),
    ("multi-head lights: each head on its own", test_multi_head),
    ("cue list: merge / replace / insert, update keeps the name", test_cue_list_modes),
    ("buttons: fade in / out and a keyboard key", test_button_fades),
    ("buttons: big tiles and icons", test_button_tiles),
    ("buttons: a speed of their own", test_button_speed),
    ("buttons: a MIDI note per button", test_button_midi),
    ("group chips flash when held; laser looks make buttons", test_group_flash_laser_button),
    ("cues keep their effects and part times; movements make buttons", test_cue_fx_parts),
    ("change a light's fixture type, keeping everything else", test_change_type),
    ("Ready? check, show versions and export", test_ready_versions),
    ("a smaller room brings its rigging back inside", test_room_fit),
    ("looks: search, and any light of these types", test_look_types),
    ("scanners, derbies and more get their own 3D model", test_more_models),
    ("RDM: the lights say what they are (fake node)", test_rdm),
    ("aiming: towers, scanners, out-of-reach, fine pan without fine tilt", test_aim_debug),
    ("rigging: turn, length, stand up, ceiling, stays inside the room", test_rig_tools),
    ("RGB-only lights: a virtual dimmer; modes that can be controlled", test_virtual_dimmer),
    ("review of PR #28: undo / load / unpatch with cue effects, queries, Ready?", test_review_fixes),
    ("steady DMX: ArtSync / sACN sync, shared looks, effect lookups cached", test_steady_dmx),
    ("tablets: screen stays awake, installs as an app, reconnects at once", test_tablets),
    ("virtual node: the whole output and RDM with no hardware", test_virtual_node),
    ("MIDI monitor: every message and what it did", test_midi_monitor),
    ("making a room: shapes, words, starter layouts, drafting", test_room_making),
    ("beat clock: taps, MIDI clock, CDJs; effects locked to the beat", test_beat_clock),
    ("sound-reactive: links, triggers, the room's tempo, never dark without sound", test_sound_reactive),
    ("sound analysis: beats, tempo and drops from a made-up track", test_sound_analysis),
    ("autopilot: a cue list plays itself on the phrase, by the room", test_autopilot),
    ("spatial effects: through the room by where the lights are", test_spatial_fx),
    )


def main() -> int:
    crashed: list[str] = []
    silent: list[str] = []
    started = time.monotonic()
    # One shared temp dir: some suites deliberately hand artefacts to
    # others (the synthetic GDTF written by the parser suite is imported
    # by the database and layout suites).  Isolation is about catching
    # exceptions, not about separate filesystems.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, suite in _suites():
            before = common.PASS + common.FAIL
            try:
                # Suites that take a temp dir get it; the rest are
                # self-contained and take no argument.
                if suite.__code__.co_argcount:
                    suite(tmp)
                else:
                    suite()
            except Exception:                       # noqa: BLE001
                crashed.append(name)
                print(f"  FAIL {name} suite crashed:\n"
                      + "    " + traceback.format_exc().replace("\n", "\n    "))
            ran = common.PASS + common.FAIL - before
            if ran == 0 and name not in crashed:
                # A suite that adds no checks has stopped testing anything
                # - usually an early `return` left behind by an edit. That
                # is silent coverage loss, so it is reported as a failure
                # rather than passing unnoticed.
                silent.append(name)
                check(f"{name} suite ran its checks", False,
                      "the suite added 0 checks - it is testing nothing")
            elif name not in crashed:
                check(f"{name} suite ran {ran} checks", True, "")
    for name, suite in _standalone_suites():
        before = common.PASS + common.FAIL
        try:
            suite()
        except Exception:                           # noqa: BLE001
            crashed.append(name)
            print(f"  FAIL {name} suite crashed:\n"
                  + "    " + traceback.format_exc().replace("\n", "\n    "))
        ran = common.PASS + common.FAIL - before
        if ran == 0 and name not in crashed:
            silent.append(name)
            check(f"{name} suite ran its checks", False,
                  "the suite added 0 checks - it is testing nothing")
        elif name not in crashed:
            check(f"{name} suite ran {ran} checks", True, "")
    try:
        check_js()
    except Exception:                               # noqa: BLE001
        crashed.append("javascript syntax")
        print("  FAIL javascript syntax suite crashed:\n"
              + "    " + traceback.format_exc().replace("\n", "\n    "))

    elapsed = time.monotonic() - started
    print(f"\n{common.PASS} passed, {common.FAIL} failed in {elapsed:.1f}s")
    if crashed:
        print("crashed suites: " + ", ".join(crashed))
    if silent:
        print("suites that ran no checks: " + ", ".join(silent))
    return 1 if (common.FAIL or crashed or silent) else 0


if __name__ == "__main__":
    sys.exit(main())

