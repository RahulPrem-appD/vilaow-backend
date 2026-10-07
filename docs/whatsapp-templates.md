# Vilaow's WhatsApp templates

<!-- Written by app/adapters/whatsapp/approval_doc.py from templates.py. Do not edit by hand. -->

WhatsApp lets a business start a conversation only with a message template that Meta has approved. Vilaow's follow-up of an introduction uses the four below.

## How to create them

In Twilio: **Messaging → Content Template Builder → Create new**, once per template and language.

1. **Template name**: as given below.
2. **Language**: as given below. Each language is its own template in Twilio.
3. **Content type**: *Text* for a template without buttons; *Quick reply* for one with buttons, using the button IDs `yes` and `no` exactly as written.
4. **Body**: paste the text exactly, keeping {{1}}, {{2}} and so on.
5. **Sample values**: the examples listed under each template.
6. **Category**: Utility. Submit for WhatsApp approval.

For the professionals' two templates, Greek is used when it is approved and English otherwise, so English alone is enough to start.

## What to send back

Each approved template's **Content SID** (it starts with HX), with its name and language. They go into the server's settings, not into chat.

## 1. `vilaow_request_received` — to the buyer

Sent to the buyer the moment they ask to be put in touch with a professional.

- **Languages**: English (`en`), Greek (`el`), French (`fr`)
- **Content type**: Text
- **Our key**: `thank_buyer` (for TWILIO_CONTENT_SIDS: `thank_buyer.<language>`)

| Blank | Holds | Sample value |
| --- | --- | --- |
| {{1}} | the buyer's first name | Sarah |
| {{2}} | the professional's name | Kostas Papadopoulos |

**English**

> Hi {{1}}, thank you for your request on Vilaow. We have passed your details to {{2}}, who will contact you soon.

**Greek**

> Γεια σας {{1}}, σας ευχαριστούμε για το αίτημά σας στο Vilaow. Στείλαμε τα στοιχεία σας στον επαγγελματία που επιλέξατε ({{2}}), ο οποίος θα επικοινωνήσει μαζί σας σύντομα.

**French**

> Bonjour {{1}}, merci pour votre demande sur Vilaow. Nous avons transmis vos coordonnées à {{2}}, qui vous contactera très prochainement.

## 2. `vilaow_new_client_request` — to the professional

Sent to the professional the moment a buyer asks to be put in touch with them.

- **Languages**: English (`en`), Greek (`el`)
- **Content type**: Text
- **Our key**: `request_to_pro` (for TWILIO_CONTENT_SIDS: `request_to_pro.<language>`)

| Blank | Holds | Sample value |
| --- | --- | --- |
| {{1}} | the professional's first name | Kostas |
| {{2}} | the buyer's full name | Sarah Mitchell |
| {{3}} | the buyer's phone | +44 7700 900123 |
| {{4}} | the buyer's email | sarah@example.com |
| {{5}} | the buyer's message, or a dash | Buying a house near Chania in the spring. |

**English**

> Hi {{1}}, you have a new client request from Vilaow. Name: {{2}}. Phone: {{3}}. Email: {{4}}. Their message: {{5}} Please contact the client as soon as you can.

**Greek**

> Γεια σας {{1}}, έχετε ένα νέο αίτημα πελάτη από το Vilaow. Όνομα: {{2}}. Τηλέφωνο: {{3}}. Email: {{4}}. Μήνυμα: {{5}} Παρακαλούμε επικοινωνήστε με τον πελάτη το συντομότερο δυνατό.

## 3. `vilaow_contact_reminder` — to the professional

Sent to the professional some hours after a request, and again each day, until they answer Yes (at most three times).

- **Languages**: English (`en`), Greek (`el`)
- **Content type**: Quick reply
- **Our key**: `remind_pro` (for TWILIO_CONTENT_SIDS: `remind_pro.<language>`)

| Blank | Holds | Sample value |
| --- | --- | --- |
| {{1}} | the professional's first name | Kostas |
| {{2}} | the buyer's full name | Sarah Mitchell |
| {{3}} | the buyer's phone | +44 7700 900123 |

**English**

> Hi {{1}}, a reminder from Vilaow: {{2}} (phone {{3}}) asked to be introduced to you. Did you contact the client?

Buttons: `Yes` (ID `yes`) · `No` (ID `no`)

**Greek**

> Γεια σας {{1}}, υπενθύμιση από το Vilaow: ο πελάτης {{2}} (τηλ. {{3}}) ζήτησε να έρθει σε επαφή μαζί σας. Επικοινωνήσατε με τον πελάτη;

Buttons: `Ναι` (ID `yes`) · `Όχι` (ID `no`)

## 4. `vilaow_contact_check` — to the buyer

Sent to the buyer two days after their request, to check the professional contacted them.

- **Languages**: English (`en`), Greek (`el`), French (`fr`)
- **Content type**: Quick reply
- **Our key**: `check_buyer` (for TWILIO_CONTENT_SIDS: `check_buyer.<language>`)

| Blank | Holds | Sample value |
| --- | --- | --- |
| {{1}} | the buyer's first name | Sarah |
| {{2}} | the professional's name | Kostas Papadopoulos |

**English**

> Hi {{1}}, a quick check from Vilaow about your request. Have you heard from {{2}} yet?

Buttons: `Yes` (ID `yes`) · `No` (ID `no`)

**Greek**

> Γεια σας {{1}}, ένας σύντομος έλεγχος από το Vilaow για το αίτημά σας. Έχει επικοινωνήσει ήδη μαζί σας ο επαγγελματίας που επιλέξατε ({{2}});

Buttons: `Ναι` (ID `yes`) · `Όχι` (ID `no`)

**French**

> Bonjour {{1}}, petite vérification de la part de Vilaow au sujet de votre demande. Avez-vous déjà eu des nouvelles de {{2}} ?

Buttons: `Oui` (ID `yes`) · `Non` (ID `no`)

## For the developer: switching it on

On Render, in the API's environment (never in chat or in the code):

- `WHATSAPP_MODE=twilio`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` (the approved WhatsApp number, as +30…)
- `TWILIO_CONTENT_SIDS` as JSON, for example `{"thank_buyer.en": "HX…", "remind_pro.el": "HX…"}`
- `TWILIO_WEBHOOK_BASE=https://vilaow-backend.onrender.com`
- `JOBS_SECRET`: 32 characters or more, random

In Twilio, the WhatsApp sender's incoming-message webhook: `https://vilaow-backend.onrender.com/api/whatsapp/twilio/inbound` (POST).

A scheduler calls `POST https://vilaow-backend.onrender.com/api/follow-ups/run` with the header `X-Jobs-Key: <JOBS_SECRET>` every ten minutes. Without it nothing after the first two messages goes out, and the admin says so.

The timings are settings too: `FOLLOWUP_FIRST_REMINDER_HOURS` (4), `FOLLOWUP_REMINDER_EVERY_HOURS` (24), `FOLLOWUP_MAX_REMINDERS` (3), `FOLLOWUP_BUYER_CHECK_HOURS` (48), and `WHATSAPP_PRO_LANGUAGE` (el).

Before switching on, the request form's consent line and its thank-you message must mention WhatsApp: today they promise an email confirmation, and with WhatsApp on, no email is sent.
