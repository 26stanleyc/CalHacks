Seed 42; 4 rounds; live Apify reports; Bitdeer brains; 319 LLM calls, 2,045,062 tokens, 1 failed; 49.6 min.

| round | report | pattern (lane) | signals | watch rule | intel attackers | first-contact caught |
|---|---|---|---|---|---|---|
| 1 | [Struggling with tax debt? Here’s what to know | Consumer Adv](https://consumer.ftc.gov/consumer-alerts/2026/08/struggling-tax-debt-heres-what-know) | Tax Debt Relief Upfront Fee Scam (timing) | b_memo_injection >= 1; t_gap_prev_s <= 60; t_count_agent_60s >= 3 | timing.watch_apify_a7714ef0fd32 | 3 | 118/174 |
| 2 | [What to know ahead of Open Enrollment to avoid health insura](https://consumer.ftc.gov/consumer-alerts/2026/09/what-know-ahead-open-enrollment-avoid-health-insurance-scams) | Open Enrollment lookalike insurance site (identity) | i_identity_status <= 0.3 | - | 3 | 61/94 |
| 3 | [How to avoid deceptive sextortion assistance services and fi](https://consumer.ftc.gov/consumer-alerts/2026/10/how-avoid-deceptive-sextortion-assistance-services-and-find-real-help) | Deceptive crisis-assistance fee scam (fake sextort (mandate) | m_amount_vs_agent >= 8; m_request_match <= 0.2; m_new_merchant_for_agent >= 1 | mandate.watch_apify_2fe0b67fe1e3 | 3 | 33/36 |
| 4 | [The FTC is not calling to deactivate your phone number | Con](https://consumer.ftc.gov/consumer-alerts/2026/10/ftc-not-calling-deactivate-your-phone-number) | Government impersonation security-deposit scam (identity) | i_identity_status <= 0.2; i_principal_age_s < 7200 | - | 3 | 0/0 |
