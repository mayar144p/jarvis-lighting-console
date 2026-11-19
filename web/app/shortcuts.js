// The keyboard's shortcuts - X for blackout, Space for GO, the number keys,
// Ctrl+Z, F1-F3, a button's or a playback's own key, the 3D view's keys -
// are off for now: the desk is played with the pointer and the touch
// screen, so a stray key can't fire the rig.  Typing in a box, Enter to
// confirm it, Esc to close a dialog or a menu, and the arrow keys on a
// slider that has the focus work as ever.
//
// One switch brings every shortcut back (localStorage "haze.shortcuts" =
// "1"): the code behind them is kept, and the checks that test them turn
// it on.
export function shortcutsOn() {
  try { return localStorage.getItem("haze.shortcuts") === "1"; } catch (e) { return false; }
}
