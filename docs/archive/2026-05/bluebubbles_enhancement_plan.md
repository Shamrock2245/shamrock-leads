# BlueBubbles Enhancement Plan for Shamrock Bail Bonds

## 1. Executive Summary
This document outlines a comprehensive plan to enhance the BlueBubbles integration within the Shamrock Bail Bonds ecosystem. The primary goals are to automate administrative tasks, provide immediate re-arrest notifications to previous clients (indemnitors/family), and drive new business prospecting. By leveraging the full capabilities of the BlueBubbles Server API and the existing Node-RED automation hub, we can significantly reduce the manual workload on agents.

## 2. Current State Analysis
*   **BlueBubbles Server**: Currently running locally, synced to a Hetzner VPS via Cloudflare tunnels (`bb-url-sync.sh`).
*   **shamrock-leads**: Has a basic `bb_private_api.py` and `imessage_automation.py` that handles inbound polling and basic agent brain routing.
*   **shamrock-node-red**: The central nervous system handling 5-channel outreach, including Twilio SMS/WhatsApp, Telegram, Email, and ElevenLabs. It has a "Closer" agent for follow-ups and an "Investigator" for IRB outreach.
*   **Gap**: The current BlueBubbles integration in `shamrock-leads` is somewhat isolated from the powerful Node-RED orchestration. Re-arrest notifications are not fully automated via iMessage, and proactive prospecting via iMessage is underutilized compared to Twilio.

## 3. Enhancement Capabilities (Leveraging BlueBubbles API)

Based on the audit of the 41 BlueBubbles repositories (specifically `bluebubbles-server` and `n8n-workflows`), we can leverage the following advanced capabilities:

### 3.1. Webhook-Driven Architecture (Moving away from Polling)
*   **Current**: `imessage_automation.py` uses a `while True` loop to poll the inbox every 30 seconds.
*   **Enhancement**: Utilize BlueBubbles Server Webhooks (`/api/v1/webhook`).
*   **Benefit**: Real-time inbound message processing, reduced server load, and immediate agent response. We will route BlueBubbles webhooks directly into the **Node-RED** hub for unified processing alongside Twilio and Telegram.

### 3.2. Advanced Message Types & Tapbacks
*   **Capability**: The Private API allows sending tapbacks (reactions), replies to specific messages, and read receipts.
*   **Enhancement**: The AI Agent Brain can now "Like" or "Emphasize" a client's message to show acknowledgment without requiring a full text response, making the bot feel more human.

### 3.3. Typing Indicators
*   **Capability**: Private API supports starting/stopping typing indicators.
*   **Enhancement**: When the AI Agent Brain is processing a complex query (e.g., checking court dates), we can trigger the typing indicator via BlueBubbles, improving UX and preventing the client from double-texting.

### 3.4. Group Chat Management
*   **Capability**: Creating and managing iMessage groups.
*   **Enhancement**: For complex bonds involving multiple indemnitors, the system can automatically create an iMessage group chat with all co-signers and the Shamrock Agent, keeping everyone in the loop simultaneously.

## 4. Specific Use Case Implementations

### 4.1. Automated Re-Arrest Notifications (The "Loyalty" Flow)
**Goal**: Notify previous indemnitors/family members immediately if a former defendant is re-arrested.
**Workflow**:
1.  **The Scout (Node-RED)** detects a new arrest via the county scrapers.
2.  **The Analyst** checks the `bonds` collection in MongoDB for a historical match (same defendant name/DOB).
3.  If a match is found, extract the previous indemnitor's phone number.
4.  **BlueBubbles Dispatch**: Send a highly personalized, empathetic iMessage:
    *   *"Hi [Indemnitor Name], this is Shamrock Bail Bonds. We wanted to let you know as a courtesy that [Defendant Name] was just booked into [County] jail. Since you helped them out last time, we wanted you to be the first to know. Let us know if you need us to look into their bond amount."*
5.  **Fallback**: If the number is not an iMessage target (BlueBubbles API returns an error or we check `availability/imessage`), fallback to Twilio SMS.

### 4.2. Proactive Prospecting (The "First Mover" Flow)
**Goal**: Reach out to new, high-value leads before competitors.
**Workflow**:
1.  **The Bounty Hunter (Node-RED)** identifies a new, unposted bond >$2,500.
2.  **The Investigator** runs an IRB deep search to find relative phone numbers.
3.  **BlueBubbles Priority**: Instead of just Twilio SMS, prioritize BlueBubbles for iPhone users (higher deliverability, blue bubble trust factor).
4.  Send a professional outreach message with a link to the Wix Intake Portal.

### 4.3. Court Date Reminders via iMessage
**Goal**: Reduce Failure to Appear (FTA) rates.
**Workflow**:
1.  Modify `court_reminders.py` and **The Court Clerk (Node-RED)**.
2.  Currently, it uses Twilio. We will add a routing layer: Check if the defendant/indemnitor has an active iMessage thread. If yes, send the 24-hour and 2-hour court reminders via BlueBubbles.

## 5. Implementation Steps (shamrock-leads repo)

1.  **Refactor `bb_private_api.py`**:
    *   Add methods for Webhook management (`POST /api/v1/webhook`).
    *   Add methods for Typing Indicators (`POST /api/v1/chat/{guid}/typing`).
    *   Add methods for Tapbacks/Reactions.
2.  **Deprecate Polling**:
    *   Phase out the `start_inbox_poller` in `imessage_automation.py`.
    *   Create a new Flask route `/api/webhooks/bluebubbles` to receive real-time events from the local Mac server.
3.  **Node-RED Integration**:
    *   Update the `bb-url-sync.sh` to also notify the Node-RED instance of the active Cloudflare URL.
    *   Ensure Node-RED can dispatch messages via the `shamrock-leads` API bridge or directly to the Cloudflare URL.
4.  **Update `first_appearance_watcher.py`**:
    *   When a bond is finally set (from 0 to >0), trigger the BlueBubbles outreach to the scraped relatives immediately.

## 6. Conclusion
By migrating from a polling architecture to a webhook-driven model and utilizing the advanced Private API features of BlueBubbles (typing indicators, tapbacks, group chats), Shamrock Bail Bonds can achieve a highly responsive, human-like automated outreach system. This will directly support the strategic goal of scaling to a 67-county operation by minimizing administrative overhead and maximizing lead conversion speed.
