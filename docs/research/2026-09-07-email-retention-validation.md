# Connected-mail retention validation

Validated on accepted production commit
`f79e0a22b6d00aa6e3d6a4d9e642f79018446733`, using the authorized synthetic
integration matter and the previously approved self-addressed message
`CSA-MAIL-VALIDATION-20260907-6184`. The message contains only a synthetic
verification marker and a no-action-required explanation.

## Process and results

1. Inspect matter Correspondence before sending. The earlier attempt remained
   marked **Delivery failed**; no successful copy was present there.
2. Send the approved message through the matter's Email Client composer to the
   approved self-recipient. Verify recipient, subject and body visually.
3. Inspect the existing capture rules. Party matching and automatic capture
   were off; the exact synthetic marker was the sole case/subject rule. No broad
   capture rule was enabled or changed.
4. Run **Scan now**. The scan examined 50 mailbox items and captured only the two
   matching provider copies. Both appeared with `.eml` download links and in
   matter Documents under **Correspondence**, stored in OneDrive.
5. Read the files using their durable provider identifiers through
   `MatterFileStore.read_matter_file_bytes`. Independently fetch the original
   Microsoft MIME messages and compare exact bytes. Parse the stored MIME and
   verify subject, recipient and body marker.
6. Resolve each provider message's parent folder. One copy was in **Sent Items**
   and one in **Inbox**, with the same Internet Message-ID. This establishes
   actual self-delivery, rather than only Microsoft accepting the send request.
7. Repeat **Scan now**. Result: **0 new, 2 already on file**. The repeat did not
   create additional retained documents.

| Provider copy | Stored bytes | SHA-256 | Exact provider-byte match |
| --- | ---: | --- | --- |
| Sent Items | 1,968 | `5c858b861ef7acac93bafe7383e65d54f07b5f03a5d0ecbdd000952511508f5f` | Yes |
| Inbox | 7,801 | `119999e52e29583ee7b1e422bc492bf6492dafa40668441f75a1f70bdd32775b` | Yes |

Both records had `storage_backend=onedrive`, category `correspondence`, and no
storage error. The Inbox copy includes additional transport headers, so the
different sizes/hashes do not indicate two sends or a retention mismatch.

## Limits and follow-up

- This establishes one Microsoft 365 self-delivery/capture roundtrip. It does
  not establish Google delivery, external-recipient delivery, attachment-bearing
  email retention, or automatic background capture.
- Self-addressed Inbox and Sent Items copies are both labeled outbound because
  direction is determined from the sender address. Two retained mailbox copies
  are grouped as a conversation; the original send activity and failed attempt
  remain separate history entries. This is a presentation/deduplication design
  consideration, not evidence of duplicate delivery.
- Byte retention and successful text retrieval are separate. Cloud `.eml` and
  Outlook MIME retrieval need format-aware decoding, including HTML-only bodies
  and exclusion of attached-file payloads, before claiming grounded email
  answers. The chat retrieval remediation tests this independently.
