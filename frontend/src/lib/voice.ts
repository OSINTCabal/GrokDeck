import { useRef, useState } from "react";
import { api } from "./api";

function pickMime() {
  if (typeof MediaRecorder === "undefined") return "";
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
  return types.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

export function speakable(text: string) {
  return text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`[\]]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 1500);
}

export function useVoiceCapture(onText: (text: string) => void | Promise<void>) {
  const [listening, setListening] = useState(false);
  const [notice, setNotice] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  function stopPlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audioRef.current = null;
  }

  async function finish(blob: Blob) {
    if (blob.size < 200) {
      setNotice("No speech was captured.");
      return;
    }
    try {
      const text = (await api.transcribe(blob)).trim();
      if (!text) {
        setNotice("No speech was heard.");
        return;
      }
      await onText(text);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Voice did not transcribe.");
    }
  }

  async function toggle() {
    if (listening && recorderRef.current) {
      recorderRef.current.stop();
      return;
    }
    setNotice("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setNotice("This window cannot use the microphone.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setNotice("The microphone is blocked.");
      return;
    }
    const mime = pickMime();
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks.current = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size) chunks.current.push(event.data);
    };
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      setListening(false);
      const blob = new Blob(chunks.current, { type: recorder.mimeType || "audio/webm" });
      void finish(blob);
    };
    recorderRef.current = recorder;
    recorder.start();
    setListening(true);
  }

  function play(blob: Blob) {
    stopPlayback();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      if (audioRef.current === audio) audioRef.current = null;
    };
    return audio.play();
  }

  return { listening, notice, setNotice, toggle, stopPlayback, play };
}
