//! hex_thrusters: lets you type a hex color in game and feeds it to FXRs as custom external values.
//!
//! Hooks the function effects read external values through (0x142b56480 in Armored Core VI 1.09.1) and answers
//! only IDs 900001-900003 (red, green, blue); every other read is left to the game. Values are served as
//! channel / 255 + 1 (1..2), so 0 keeps meaning "nothing provides this" for effects running without the DLL.
//!
//! Input: press F8 while the game window is focused, then type 6 hex digits (0-9 on the top row or numpad, A-F).
//! The color applies after the 6th digit. Backspace deletes a digit; Esc or F8 again cancels; 10 s idle cancels.
//! Log: Game\debug-logs\hex_thrusters.log

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use retour::RawDetour;

const GET_ADDR: usize = 0x142b56480;
// mov [rsp+18],r8d
const GET_PROLOGUE: [u8; 5] = [0x44, 0x89, 0x44, 0x24, 0x18];

const EXT_RED: i32 = 900001;
const EXT_BLUE: i32 = 900003;
const DEFAULT_COLOR: u32 = 0xFF3030;

const VK_BACK: i32 = 0x08;
const VK_ESCAPE: i32 = 0x1B;
const VK_START: i32 = 0x77; // F8
const ENTRY_TIMEOUT: Duration = Duration::from_secs(10);

static ORIG_GET: AtomicUsize = AtomicUsize::new(0);
static COLOR: AtomicU32 = AtomicU32::new(DEFAULT_COLOR);
static LOG: OnceLock<Mutex<File>> = OnceLock::new();

#[link(name = "user32")]
unsafe extern "system" {
    fn GetAsyncKeyState(vkey: i32) -> i16;
    fn GetForegroundWindow() -> isize;
    fn GetWindowThreadProcessId(hwnd: isize, pid: *mut u32) -> u32;
}

fn log(msg: &str) {
    if let Some(Ok(mut f)) = LOG.get().map(|l| l.lock()) {
        let _ = writeln!(f, "{msg}");
    }
}

unsafe extern "system" fn get_impl(block: *mut u8, out: *mut u32, id: i32) -> *mut u32 {
    let orig: unsafe extern "system" fn(*mut u8, *mut u32, i32) -> *mut u32 =
        unsafe { std::mem::transmute(ORIG_GET.load(Ordering::Relaxed)) };
    let result = unsafe { orig(block, out, id) };
    if (EXT_RED..=EXT_BLUE).contains(&id) && !out.is_null() {
        let shift = 8 * (EXT_BLUE - id) as u32; // red = bits 16-23, green 8-15, blue 0-7
        let channel = (COLOR.load(Ordering::Relaxed) >> shift) & 0xFF;
        unsafe { *out = (1.0 + channel as f32 / 255.0).to_bits() };
    }
    result
}

fn install_hook() -> Result<(), String> {
    let prologue = unsafe { std::slice::from_raw_parts(GET_ADDR as *const u8, 5) };
    if prologue != GET_PROLOGUE {
        return Err(format!("unexpected bytes at {GET_ADDR:#x} (not 1.09.1?)"));
    }
    let detour = unsafe { RawDetour::new(GET_ADDR as *const (), get_impl as *const ()) }.map_err(|e| e.to_string())?;
    ORIG_GET.store(detour.trampoline() as *const () as usize, Ordering::Relaxed);
    unsafe { detour.enable() }.map_err(|e| e.to_string())?;
    // The hook stays installed for the life of the process.
    std::mem::forget(detour);
    Ok(())
}

fn game_focused() -> bool {
    let mut pid = 0;
    unsafe { GetWindowThreadProcessId(GetForegroundWindow(), &mut pid) };
    pid == std::process::id()
}

fn hex_digit(vk: i32) -> Option<char> {
    match vk {
        0x30..=0x39 => char::from_digit((vk - 0x30) as u32, 16),
        0x60..=0x69 => char::from_digit((vk - 0x60) as u32, 16), // numpad 0-9
        0x41..=0x46 => Some((b'A' + (vk - 0x41) as u8) as char),
        _ => None,
    }
}

fn input_loop() {
    let keys: Vec<i32> = [VK_START, VK_ESCAPE, VK_BACK].into_iter().chain((0x30..=0x39).chain(0x60..=0x69).chain(0x41..=0x46)).collect();
    let mut down = vec![false; keys.len()];
    let mut entry: Option<(String, Instant)> = None;
    loop {
        std::thread::sleep(Duration::from_millis(10));
        let focused = game_focused();
        for (i, &vk) in keys.iter().enumerate() {
            let now_down = focused && unsafe { GetAsyncKeyState(vk) } < 0;
            let pressed = now_down && !down[i];
            down[i] = now_down;
            if !pressed {
                continue;
            }
            match (vk, entry.as_mut()) {
                (VK_START, None) => {
                    entry = Some((String::new(), Instant::now()));
                    log("entry started");
                }
                (VK_START | VK_ESCAPE, Some(_)) => {
                    entry = None;
                    log("entry cancelled");
                }
                (VK_BACK, Some((digits, last))) => {
                    digits.pop();
                    *last = Instant::now();
                }
                (_, Some((digits, last))) => {
                    if let Some(c) = hex_digit(vk) {
                        digits.push(c);
                        *last = Instant::now();
                        if digits.len() == 6 {
                            if let Ok(color) = u32::from_str_radix(digits, 16) {
                                COLOR.store(color, Ordering::Relaxed);
                                log(&format!("color set to #{digits}"));
                            }
                            entry = None;
                        }
                    }
                }
                _ => {}
            }
        }
        if entry.as_ref().is_some_and(|(_, last)| last.elapsed() > ENTRY_TIMEOUT) {
            entry = None;
            log("entry timed out");
        }
    }
}

fn init() {
    let dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.join("debug-logs")))
        .unwrap_or_else(|| PathBuf::from("debug-logs"));
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(f) = OpenOptions::new().create(true).write(true).truncate(true).open(dir.join("hex_thrusters.log")) {
        let _ = LOG.set(Mutex::new(f));
    }
    match install_hook() {
        Ok(()) => log(&format!("hook installed, color #{DEFAULT_COLOR:06X}, press F8 then 6 hex digits")),
        Err(e) => {
            log(&format!("not active: {e}"));
            return;
        }
    }
    std::thread::spawn(input_loop);
}

#[unsafe(no_mangle)]
extern "system" fn DllMain(_module: *mut u8, reason: u32, _reserved: *mut u8) -> i32 {
    const DLL_PROCESS_ATTACH: u32 = 1;
    if reason == DLL_PROCESS_ATTACH {
        init();
    }
    1
}
