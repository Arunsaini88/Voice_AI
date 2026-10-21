// Native dictation engine: microphone -> 16 kHz mono PCM -> backend WebSocket.
//
// Speaks the same protocol as the browser client (see docs/DEVELOPMENT.md):
// binary audio frames, then {"type":"stop"}, then wait for "done". Every
// server message is forwarded to the webview unchanged as a `dictation`
// event, so the React hook treats both engines alike.
//
// Audio source is the default input device via cpal, or a WAV file when
// VN_FAKE_MIC is set (for machines without a microphone and for tests).
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use futures_util::{SinkExt, StreamExt};
use tauri::{AppHandle, Emitter};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

const TARGET_RATE: u32 = 16_000;
const CHUNK_SAMPLES: usize = 1_600; // 100 ms at 16 kHz
const FLUSH_TIMEOUT: Duration = Duration::from_secs(5);

pub struct Session {
    stop: Arc<AtomicBool>,
    done: Arc<AtomicBool>,
}

impl Session {
    pub fn start(app: AppHandle, api_url: String, token: String, lang: String) -> Result<Session, String> {
        let stop = Arc::new(AtomicBool::new(false));
        let done = Arc::new(AtomicBool::new(false));
        let (stop_c, done_c) = (stop.clone(), done.clone());
        std::thread::Builder::new()
            .name("dictation".into())
            .spawn(move || {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime");
                if let Err(e) = rt.block_on(run(&app, api_url, token, lang, stop_c)) {
                    emit(&app, serde_json::json!({ "type": "error", "text": e }));
                }
                emit(&app, serde_json::json!({ "type": "closed" }));
                done_c.store(true, Ordering::SeqCst);
            })
            .map_err(|e| e.to_string())?;
        Ok(Session { stop, done })
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub fn is_done(&self) -> bool {
        self.done.load(Ordering::SeqCst)
    }
}

fn emit(app: &AppHandle, payload: serde_json::Value) {
    let _ = app.emit("dictation", payload);
}

pub fn default_mic_name() -> Option<String> {
    if let Ok(path) = std::env::var("VN_FAKE_MIC") {
        return Some(format!("WAV file: {path}"));
    }
    cpal::default_host().default_input_device()?.name().ok()
}

async fn run(app: &AppHandle, api_url: String, token: String, lang: String, stop: Arc<AtomicBool>) -> Result<(), String> {
    let base = api_url.trim_end_matches('/');
    let ws_base = if let Some(rest) = base.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = base.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        return Err(format!("Server URL must start with http:// or https:// (got {base})"));
    };
    let url = format!(
        "{ws_base}/dictate/stream?lang={}&encoding=linear16&sample_rate={TARGET_RATE}&token={}",
        urlencoding::encode(&lang),
        urlencoding::encode(&token)
    );

    let (ws, _) = tokio_tungstenite::connect_async(url.as_str())
        .await
        .map_err(|e| format!("Could not reach the server at {ws_base}: {e}"))?;
    let (mut tx, mut rx) = ws.split();

    // Audio arrives on this channel from the capture thread / cpal callback.
    let (audio_tx, mut audio_rx) = mpsc::channel::<Vec<i16>>(64);
    let capture = start_capture(audio_tx, stop.clone())?;

    let mut stop_sent = false;
    let mut deadline: Option<Instant> = None;
    let mut tick = tokio::time::interval(Duration::from_millis(50));

    loop {
        tokio::select! {
            Some(chunk) = audio_rx.recv(), if !stop_sent => {
                let mut bytes = Vec::with_capacity(chunk.len() * 2);
                for s in chunk { bytes.extend_from_slice(&s.to_le_bytes()); }
                tx.send(Message::Binary(bytes.into())).await.map_err(|e| format!("send failed: {e}"))?;
            }
            msg = rx.next() => {
                match msg {
                    Some(Ok(Message::Text(text))) => {
                        let value: serde_json::Value = serde_json::from_str(&text).unwrap_or(serde_json::json!({"type":"raw","text":text.to_string()}));
                        let kind = value.get("type").and_then(|t| t.as_str()).unwrap_or("").to_string();
                        emit(app, value);
                        if kind == "done" { break; }
                    }
                    Some(Ok(Message::Close(frame))) => {
                        if let Some(f) = frame {
                            let code: u16 = f.code.into();
                            if code != 1000 {
                                emit(app, serde_json::json!({"type":"close","code":code,"reason":f.reason.to_string()}));
                            }
                        }
                        break;
                    }
                    Some(Ok(_)) => {}
                    Some(Err(e)) => return Err(format!("connection lost: {e}")),
                    None => break,
                }
            }
            _ = tick.tick() => {
                if stop.load(Ordering::SeqCst) && !stop_sent {
                    capture.stop();
                    // Drain whatever the capture thread already produced.
                    while let Ok(chunk) = audio_rx.try_recv() {
                        let mut bytes = Vec::with_capacity(chunk.len() * 2);
                        for s in chunk { bytes.extend_from_slice(&s.to_le_bytes()); }
                        let _ = tx.send(Message::Binary(bytes.into())).await;
                    }
                    tx.send(Message::Text(r#"{"type":"stop"}"#.into())).await.map_err(|e| format!("send failed: {e}"))?;
                    stop_sent = true;
                    deadline = Some(Instant::now() + FLUSH_TIMEOUT);
                }
                if let Some(d) = deadline {
                    if Instant::now() > d { break; } // server went quiet: keep what we have
                }
            }
        }
    }
    let _ = tx.close().await;
    Ok(())
}

// ----------------------------------------------------------------- capture

struct Capture {
    stop: Arc<AtomicBool>,
    _stream: Option<cpal::Stream>,
}

impl Capture {
    fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

impl Drop for Capture {
    fn drop(&mut self) {
        self.stop();
    }
}

fn start_capture(tx: mpsc::Sender<Vec<i16>>, stop: Arc<AtomicBool>) -> Result<Capture, String> {
    if let Ok(path) = std::env::var("VN_FAKE_MIC") {
        let flag = Arc::new(AtomicBool::new(false));
        let f = flag.clone();
        std::thread::spawn(move || fake_mic(path, tx, f));
        // The session's stop flag stops the fake mic via Capture::stop.
        return Ok(Capture { stop: flag, _stream: None });
    }
    let _ = stop; // the real stream stops when dropped
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .ok_or_else(|| "No microphone found".to_string())?;
    let config = device
        .default_input_config()
        .map_err(|e| format!("Microphone not usable: {e}"))?;
    let channels = config.channels() as usize;
    let in_rate = config.sample_rate().0;
    let mut resampler = Resampler::new(in_rate, TARGET_RATE);
    let mut pending: Vec<i16> = Vec::with_capacity(CHUNK_SAMPLES * 2);
    let flag = Arc::new(AtomicBool::new(false));
    let f = flag.clone();

    let err_fn = |e| eprintln!("microphone stream error: {e}");
    let mut push = move |mono: &[f32]| {
        if f.load(Ordering::SeqCst) {
            return;
        }
        pending.extend(resampler.process(mono));
        while pending.len() >= CHUNK_SAMPLES {
            let chunk: Vec<i16> = pending.drain(..CHUNK_SAMPLES).collect();
            let _ = tx.try_send(chunk); // never block the audio thread; drop if the sender is backed up
        }
    };

    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => device.build_input_stream(
            &config.into(),
            move |data: &[f32], _| push(&to_mono(data, channels)),
            err_fn,
            None,
        ),
        cpal::SampleFormat::I16 => device.build_input_stream(
            &config.into(),
            move |data: &[i16], _| {
                let f: Vec<f32> = data.iter().map(|&s| s as f32 / 32768.0).collect();
                push(&to_mono(&f, channels))
            },
            err_fn,
            None,
        ),
        cpal::SampleFormat::U16 => device.build_input_stream(
            &config.into(),
            move |data: &[u16], _| {
                let f: Vec<f32> = data.iter().map(|&s| (s as f32 - 32768.0) / 32768.0).collect();
                push(&to_mono(&f, channels))
            },
            err_fn,
            None,
        ),
        other => return Err(format!("Unsupported microphone sample format {other:?}")),
    }
    .map_err(|e| format!("Could not open microphone: {e}"))?;
    stream.play().map_err(|e| format!("Could not start microphone: {e}"))?;
    Ok(Capture { stop: flag, _stream: Some(stream) })
}

fn to_mono(data: &[f32], channels: usize) -> Vec<f32> {
    if channels <= 1 {
        return data.to_vec();
    }
    data.chunks(channels).map(|frame| frame.iter().sum::<f32>() / channels as f32).collect()
}

/// Linear-interpolation resampler, good enough for speech going to an ASR.
struct Resampler {
    ratio: f64,   // input samples per output sample
    pos: f64,     // fractional read position into the carried buffer
    carry: Vec<f32>,
}

impl Resampler {
    fn new(in_rate: u32, out_rate: u32) -> Self {
        Self { ratio: in_rate as f64 / out_rate as f64, pos: 0.0, carry: Vec::new() }
    }

    fn process(&mut self, input: &[f32]) -> Vec<i16> {
        if (self.ratio - 1.0).abs() < 1e-9 {
            return input.iter().map(|&s| to_i16(s)).collect();
        }
        self.carry.extend_from_slice(input);
        let mut out = Vec::with_capacity((input.len() as f64 / self.ratio) as usize + 1);
        while (self.pos + 1.0) < self.carry.len() as f64 {
            let i = self.pos as usize;
            let frac = (self.pos - i as f64) as f32;
            let s = self.carry[i] * (1.0 - frac) + self.carry[i + 1] * frac;
            out.push(to_i16(s));
            self.pos += self.ratio;
        }
        let consumed = self.pos as usize;
        self.carry.drain(..consumed);
        self.pos -= consumed as f64;
        out
    }
}

fn to_i16(s: f32) -> i16 {
    (s.clamp(-1.0, 1.0) * 32767.0) as i16
}

/// Reads a WAV file and paces it like a live microphone. 16 kHz mono i16 is
/// passed through; anything else is converted.
fn fake_mic(path: String, tx: mpsc::Sender<Vec<i16>>, stop: Arc<AtomicBool>) {
    let mut reader = match hound::WavReader::open(&path) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("VN_FAKE_MIC: cannot open {path}: {e}");
            return;
        }
    };
    let spec = reader.spec();
    let channels = spec.channels as usize;
    let samples: Vec<f32> = match spec.sample_format {
        hound::SampleFormat::Int => {
            let max = (1u32 << (spec.bits_per_sample - 1)) as f32;
            reader.samples::<i32>().filter_map(Result::ok).map(|s| s as f32 / max).collect()
        }
        hound::SampleFormat::Float => reader.samples::<f32>().filter_map(Result::ok).collect(),
    };
    let mono = to_mono(&samples, channels);
    let mut rs = Resampler::new(spec.sample_rate, TARGET_RATE);
    let pcm = rs.process(&mono);

    let chunk = CHUNK_SAMPLES * 4; // 400 ms per send, paced in real time
    for piece in pcm.chunks(chunk) {
        if stop.load(Ordering::SeqCst) {
            return;
        }
        if tx.blocking_send(piece.to_vec()).is_err() {
            return;
        }
        std::thread::sleep(Duration::from_millis((piece.len() as u64 * 1000) / TARGET_RATE as u64));
    }
    // End of file: stay "open" (silent) until the user stops, like a real mic.
    while !stop.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(100));
    }
}
