# Synthetic calendar review

This workflow is for Iago's isolated test profile only. It connects no real account.

1. Discover the enabled calendar tools and check the test_calendar runtime capability.
2. Read synthetic-a availability using list_events.
3. Ask the user for the review title and start time if absent; prepare draft_event.
4. Present the exact account and event payload. Request create_event through the shared executor.
5. Application policy applies configured standing authorization or an exact-action confirmation.
6. Report the returned operation outcome. Reconcile uncertain writes; never blindly retry.

These instructions cannot enable tools, authorize writes, access secrets or install a runtime.
