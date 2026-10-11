# DECISIONS.md

1. **Indexes.** I created a unique index on `employees.emp_code` for duplicate protection, a unique index on `(emp_code, date)` for one attendance record per day, `(emp_code, punch_in)` for punch-out lookup, `(date, emp_code, status)` for attendance listing, and department indexes for employee filters/headcount. I considered a broad compound index for every analytics query, but rejected it because the dataset is modest and too many indexes slow writes.

2. **Punch-in race.** The code does not do check-then-insert. It builds the attendance document and inserts directly. If two identical punch-ins arrive together, MongoDB's unique `(emp_code, date)` index allows one insert and rejects the other. The rejected request becomes `409`.

3. **Ties.** The late leaderboard uses standard competition ranking. If two employees tie at the cutoff rank, both are returned because the `limit` applies to rank, not row count.

4. **Headcount.** Department summary starts from `employees`, filters by `joined_on <= month end`, then looks up logs. Because employees are the base collection, people with zero logs are still counted.

5. **100x data change.** I would add more targeted compound indexes after checking real `explain` output, move expensive trend reporting to precomputed daily summaries, and add integration tests with a large seeded dataset.
