# Legal Notice & Terms of Use — doch1

> **⚠️ READ THIS BEFORE YOU USE doch1.**
> **DO NOT use this tool to fake attendance.** A presence report (דו"ח 1 / DOCH1) is an official declaration to a military authority. Submitting a false one can constitute fraud and a serious offense — it can expose you to court-martial, criminal prosecution, and possibly imprisonment. doch1 is for soldiers who *are actually present* and just want to skip the busywork. Nothing else.
>
> By installing, running, or using doch1 you accept everything in this document. **If you do not agree, do not use it.**

---

## 1. Purpose & Scope

*What doch1 is, and what this document is.*

**doch1** is an unofficial, open-source command-line / TUI tool that automates submitting the Israeli army daily presence report (**דו"ח 1**, "DOCH1") on the official site `one.prat.idf.il`. It logs in using *your own* Microsoft Entra military account and talks to the site's API to file the report on your behalf, optionally on a daily schedule.

This document (`LEGAL.md`) explains the legal and ethical terms, risks, and disclaimers that apply when you use doch1. It is part of the project alongside the `LICENSE` (MIT). Where this document and the MIT `LICENSE` overlap (warranty, liability), they are meant to be consistent; the MIT `LICENSE` governs the software grant itself.

---

## 2. This Is Not Legal Advice

*Informational only.*

Nothing here is legal advice, and the authors are not your lawyers. The legal references below are described in plain language for a layperson and are **approximate** — statute names and the way they might apply are given so you can look things up, not as a legal opinion. Before you use doch1, if you have any doubt, consult a qualified Israeli lawyer, and — if you are a soldier — your unit's legal channels or the Military Advocate General's (**פצ"ר**) office. They, not this file, are the authority.

---

## 3. No Affiliation, No Endorsement

*doch1 is an independent project.*

doch1 is **not** affiliated with, authorized by, endorsed, sponsored by, or connected to the Israel Defense Forces (IDF / צה"ל), the Israeli Ministry of Defense, Microsoft, the operators of Prat / `one.prat.idf.il`, or any government body. All product names, logos, and trademarks belong to their respective owners and are used here only to identify what the tool interacts with (nominative use). No such organization has reviewed, approved, or supports this project.

---

## 4. Intended Use

*Remove busywork — for people who are genuinely present.*

doch1 exists for one purpose: to save time for soldiers who **are genuinely present and entitled to report so**, by automating a tedious daily form. That's it. It is **not** a tool to fabricate, simulate, pre-schedule, or backfill presence that isn't true, and it is not designed or intended to be used that way. Using it for anything else is outside its purpose and entirely on you.

**The canonical, blessed pattern is AI-native and human-in-the-loop:** a soldier who **is genuinely present** instructs their AI agent (e.g. the Hermes agent or Claude Code) **each morning** to file that day's doch1. This is the recommended pattern precisely *because* the human affirms their presence anew every morning at the moment they trigger it — an explicit, per-day instruction to automate a **truthful** report. The automation does the busywork; the daily human confirmation keeps the declaration honest.

**A caution on unattended scheduling:** fully-unattended scheduling (e.g. cron) files "present" **without** a daily human confirmation. It should therefore only be enabled by someone who is reliably present on the scheduled days, and it must **never** be used to assert presence on days you are — or may be — absent. This does not weaken §5: truthfulness remains mandatory regardless of how a report is triggered.

---

## 5. ⚠️ NO FALSIFICATION — Truthfulness Is Mandatory

> ### This is the most important section in this document.
>
> **Every report you submit must be true.**
>
> Run doch1 **only** on days you are genuinely present / at base as required. **Never** use it to:
> - auto-submit "present" for days you are absent,
> - pre-schedule or backfill presence you did not have, or
> - mask, hide, or paper over an absence.

A presence report is an **official declaration to a military authority**. Submitting a false one is dishonest reporting to the military and can be a **serious offense** — potentially exposing you to **military disciplinary action and criminal prosecution, including possible imprisonment** — completely independent of anything this tool does or doesn't do. The convenience of automation does **not** change the fact that *you* are personally declaring "I was present" each time.

**Legal frameworks that may apply (approximate, layperson level — not a precise charge):**

- **Military Justice Law, 5715-1955** — *חוק השיפוט הצבאי, התשט"ו-1955* — the law defining military offenses and courts-martial. False reporting, deceiving a superior, or conduct unbecoming may be prosecuted under it.
- **Penal Law, 5737-1977** — *חוק העונשין, התשל"ז-1977* — general fraud / false-statement-type offenses that may also apply.

The exact section numbers and how they apply are **beyond the scope of this document** and are deliberately not stated here, because pinning a precise provision at a layperson level would be misleading. Only a qualified lawyer or your unit's legal channels / the Military Advocate General's (**פצ"ר**) office can tell you how the law applies to you.

**The authors condemn any use of doch1 to falsify presence, accept no responsibility for it, and you alone bear full responsibility for the truth of every submission.**

---

## 6. Lawful Use & Your Responsibility

*You own every report and every consequence.*

You are **solely and exclusively responsible** for:

- the **accuracy and truthfulness** of every report doch1 submits on your behalf,
- using the tool **lawfully** and in accordance with all applicable laws, military orders, regulations, and information-security rules that apply to you, and
- **any and all consequences** of using it.

The authors cannot and do not verify whether any report you submit is true, authorized, or lawful. That is your job, every single time.

---

## 7. Terms of Use & Automated-Access Risk

*Automating the site may break its rules and may implicate computer-access law.*

`one.prat.idf.il` is an official government system. Accessing it with an automated script — rather than by hand through the intended interface — and submitting reports programmatically **may violate the site's Terms of Use**, and may implicate **Israel's Computers Law, 5755-1995** (*חוק המחשבים, התשנ"ה-1995*), which addresses unlawful penetration of, and interference with, computer systems. Consequences can range from your access being blocked or revoked to criminal exposure. By using doch1 you knowingly **accept that risk yourself**.

---

## 8. Reverse-Engineering Disclaimer

*The API is undocumented and unofficial.*

doch1 talks to an **undocumented, unofficial API** that was derived by observing how the `one.prat.idf.il` website behaves. This means:

- the API can **change, break, or be blocked at any time**, without notice;
- a submission can **fail silently** — meaning you might believe a report was filed when it was **not**. Do not assume success; verify; and
- interacting with the system this way may itself be **restricted** by the site's terms or by law (see §7).

Always confirm your reports were actually accepted through official channels. Treating doch1's output as proof of a filed report is your risk.

---

## 9. Credentials, Session Data & Security

*Your real military login. Your responsibility to protect it.*

doch1 uses **your real military Microsoft Entra credentials and session** to authenticate. You should understand:

- Authentication tokens, cookies, and session data are stored **locally on your machine**, and securing them is **your responsibility**. Do not commit them to version control, share them, or leave them exposed.
- The authors **never receive, transmit, or have access to** your credentials, tokens, or session data. They stay on your device.
- Automating logins to a sensitive account carries real risks, including **account lockout, conditional-access blocks, MFA prompts or failures, flagging of anomalous logins**, and — if your machine is compromised or you mishandle the files — **exposure of a sensitive military account**.

If you are not prepared to secure these credentials, do not use this tool.

---

## 10. "AS IS" — No Warranty

*Same disclaimer as the MIT LICENSE.*

doch1 is provided **"AS IS" and "AS AVAILABLE", without warranty of any kind**, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, accuracy, reliability, and non-infringement. The authors do not warrant that the tool will work, will keep working, will submit reports correctly, or will not cause harm. This is consistent with the warranty disclaimer in the project's MIT `LICENSE`.

---

## 11. Limitation of Liability

*The authors are not liable for what happens when you use this.*

To the maximum extent permitted by law, the authors and contributors are **not liable** for any direct, indirect, incidental, special, consequential, or disciplinary damages of any kind arising out of or related to your use of (or inability to use) doch1 — including, without limitation, **military disciplinary or criminal proceedings, account loss or lockout, credential compromise, data loss, or missed/failed/incorrect report submissions** — even if advised of the possibility. This is consistent with the limitation in the project's MIT `LICENSE`.

---

## 12. Indemnification

*If your use causes a claim, that's on you, not the authors.*

You agree to **hold the authors and contributors harmless** from and against any claims, demands, losses, liabilities, or expenses arising out of or related to your use or misuse of doch1, including any breach of these terms, any law, any military order or regulation, or the site's Terms of Use.

---

## 13. Governing Law & Jurisdiction

*Israeli law, Israeli courts.*

This notice is governed by the **laws of the State of Israel**, with **exclusive jurisdiction in the competent courts of Israel** — consistent with the project's MIT `LICENSE`. This is a **unilateral choice-of-law statement by the author**, not a negotiated contract. It does **not** override mandatory Israeli law, binding military orders or regulations, or the binding Terms of Use of `one.prat.idf.il`. The primary statutes referenced in this document are the **Computers Law, 5755-1995** (*חוק המחשבים*) and the **Military Justice Law, 5715-1955** (*חוק השיפוט הצבאי*).

---

## 14. Prior Art

*Context, for honesty's sake.*

A similar public project exists — [`y-golde/doch1`](https://github.com/y-golde/doch1). It is noted here neutrally as prior art. It shipped without a notice like this one; doch1's disclaimers are deliberate, because automating a military system is sensitive.

---

## 15. Acceptance

*Using doch1 means you accept all of this.*

By installing, running, or using doch1, you acknowledge that you have **read, understood, and accepted** this entire notice — most importantly the requirement that **every report you submit must be true**. **If you do not agree, do not install, run, or use doch1.**
