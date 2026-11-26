<!-- DRAFT example written by Claude Code. Shreya: replace with your own. -->

# SmileWell clinics: WhatsApp appointment reminders

Patients miss ~18% of appointments. We want WhatsApp reminders 24h and 2h before each visit, with a
"confirm / reschedule" reply. Pilot in 3 clinics first (Andheri, Powai, Thane), then all 14.

## Work
- Hook into the booking system's appointment export (nightly CSV today, maybe an API later).
- WhatsApp Business API account + message templates need Meta approval before anything goes out.
- Reminder scheduler service that sends at the right times and handles replies.
- Reschedule flow: patient picks a new slot from a short list.
- Front-desk dashboard showing confirmations for the day.
- Consent: patients must opt in at the front desk; we need the consent text reviewed by legal.
- Poster + counter card for the clinics explaining the reminders.
- Measure no-show rate before vs after.

## Timing
- Templates submitted for approval by 4 May.
- Pilot starts 18 May in the 5 pilot clinics.
