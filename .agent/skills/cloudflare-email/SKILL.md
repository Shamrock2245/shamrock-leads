---
name: cloudflare-email
description: "Cloudflare Email Service for transactional email. Use when configuring email routing, sending transactional emails via Workers, or setting up domain email for shamrockbailbonds.biz."
source: "https://github.com/cloudflare/skills (cloudflare-email-service/SKILL.md)"
compatibility: Requires Cloudflare account with Email Routing enabled.
---

# Cloudflare Email Service

## Overview

Configure and use Cloudflare's email infrastructure for transactional email delivery, email routing, and domain email management.

## Capabilities

### Email Routing
Route incoming emails to different destinations based on rules:
- Forward `admin@shamrockbailbonds.biz` → Gmail
- Forward `intake@shamrockbailbonds.biz` → GAS webhook processing
- Catch-all for unknown addresses

### Email Workers
Process incoming emails programmatically:
```javascript
export default {
  async email(message, env, ctx) {
    const { from, to } = message;
    // Process intake emails
    if (to === "intake@shamrockbailbonds.biz") {
      // Forward to GAS or process directly
      await message.forward("admin@shamrockbailbonds.biz");
    }
  }
}
```

### Transactional Email (via Workers + Bindings)
Send transactional emails from Workers:
```javascript
// wrangler.toml
// [[send_email]]
// name = "SEND_EMAIL"

export default {
  async fetch(request, env) {
    const msg = createMimeMessage();
    msg.setSender({ name: "Shamrock Bail Bonds", addr: "no-reply@shamrockbailbonds.biz" });
    msg.setRecipient("client@example.com");
    msg.setSubject("Your Bond Paperwork is Ready");
    msg.addMessage({ contentType: "text/html", data: htmlBody });

    const message = new EmailMessage(
      "no-reply@shamrockbailbonds.biz",
      "client@example.com",
      msg.asRaw()
    );
    await env.SEND_EMAIL.send(message);
  }
}
```

## Domain Setup Requirements

1. Domain must be added to Cloudflare (shamrockbailbonds.biz ✅)
2. MX records configured for Cloudflare Email Routing
3. SPF, DKIM, and DMARC records for deliverability
4. Email Routing enabled in Cloudflare dashboard

## ShamrockLeads Use Cases

| Use Case | Implementation |
|----------|---------------|
| Court reminder emails | Transactional via Workers |
| Intake confirmation | Email Workers processing |
| Signing link delivery | Email routing + Workers |
| Admin notifications | Forward to Gmail |

## Key Principles

1. Always verify domain DNS records before sending
2. Include proper SPF/DKIM/DMARC for deliverability
3. Comply with 10DLC standards for email+SMS campaigns
4. Never send from unverified domains
