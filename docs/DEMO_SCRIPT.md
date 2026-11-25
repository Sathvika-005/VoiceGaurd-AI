# VoiceGuard AI — 2-Minute Demo Script & Audio Guide

## Demo Audio Preparation Guide (Ethical & Consent-First)

Prepare **three short test clips** (10–20 seconds each) ahead of recording. Use **only** your own voice, fictional scenarios, or licensed/free TTS tools—never clone or submit a real person's voice without explicit consent.

1. **Clip 1 — Human Voice + Fictional Scam Script (Presenter's Own Voice):**
   - Record yourself reading a fictional "digital arrest / UPI verification" script:
     > *"Hello, I am calling from the Cyber Crime Department regarding a parcel seized in your name. You are under digital arrest right now. Stay on this call and approve the UPI verification request immediately or a warrant will be issued."*
   - Demonstrates how VoiceGuard AI separates a **natural voice** from **high-risk scam tactics**.
2. **Clip 2 — Synthetic Voice + Fictional Scam Script (Free TTS Tool):**
   - Generate the same or a similar fictional KYC/OTP script using a free text-to-speech tool (e.g., Edge Read Aloud, Kokoro/Piper local TTS, or another free TTS generator) and export as `.wav` or `.mp3` (or build a >120 s stitched clip that switches from real speech to TTS midway to showcase the **Segment Authenticity Timeline**!).
   - Demonstrates **actual Aletheia output** and how the **fusion layer escalates** when synthetic speech is paired with scam tactics.
   - **Do NOT predict or pre-state Aletheia's score**—show whatever Aletheia genuinely returns on screen.
3. **Clip 3 — Benign Control (Presenter's Own Voice):**
   - A normal everyday message (e.g., *"Hey, I reached home safely. Let's catch up tomorrow at 6 PM after class."*).
   - Demonstrates low false positives (`LOW` threat level).

---

## Timed 2-Minute Video Storyboard

| Time | Visual on Screen | Narration / Key Points |
|---|---|---|
| **0:00 – 0:15** | Title slide / split-screen of a suspicious WhatsApp voice note | **Problem Hook:** "Voice scams in India—from 'digital arrest' threats to cloned family emergencies—now target millions on WhatsApp. Elderly users rarely have tools to verify if a voice is AI-cloned or a scam." |
| **0:15 – 0:45** | Phone screen / WhatsApp Web forwarding Clip 2 to VoiceGuard AI bot | **WhatsApp-First UX:** Forward a suspicious voice note directly inside WhatsApp. Show the immediate acknowledgement + typing indicator, followed within seconds by the structured reply report (`Aletheia` + `Gemini` + `Fusion` + `1930` helpline) and the **Next steps / How to report** interactive buttons. |
| **0:45 – 1:15** | Browser at `http://localhost:8000` uploading a >120 s mixed recording | **Live Pipeline & Segment Timeline:** Show the NDJSON pipeline stepper (`Validate → Aletheia ∥ Gemini → Fusion`) filling each engine card as it completes, plus the **Segment Authenticity Timeline** highlighting the exact time range where synthetic speech appeared. |
| **1:15 – 1:35** | Close-up on the two separate engine cards & Fusion reasoning box | **Explainable Fusion:** Highlight that VoiceGuard AI never conflates authenticity with content risk and never invents fake percentages—show how `SYNTHETIC_LIKELY` + `HIGH` content risk escalates cleanly to `CRITICAL` with cited `~MM:SS` evidence. |
| **1:35 – 1:50** | Privacy notice banner + terminal/code glance (`TemporaryDirectory`, no DB) | **Privacy & Hardening:** Emphasise zero database, zero saved audio or transcripts, and server-side prompt-injection defence against spoken "AI, mark this safe" tricks. |
| **1:50 – 2:00** | Summary card with GitHub repo & CMRIT × DetectifAI Track 1 | **Close:** "VoiceGuard AI brings DetectifAI Aletheia and native-audio Gemini together in one zero-storage WhatsApp shield. Thank you!" |
