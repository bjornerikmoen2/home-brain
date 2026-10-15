---
type: vehicle
example: true
name: Family car
reg: AB 12345
make: Example
model: Model Y
year: 2021
tires:
  winter: {size: 235/55R18, studded: false, brand: Example Ice, bought: 2022-10, tread_mm: 4.5, measured: 2026-04-10}
  summer: {size: 235/55R18, brand: Example Sport, bought: 2024-04, tread_mm: 6.0, measured: 2026-04-10}
tire_storage: Tire hotel at Example Dekk
tire_changes: [2025-10-28, 2026-04-10]
due:
  - {date: 2027-03-01, title: EU-kontroll, warn_days: 60}
  - {date: 2026-12-01, title: Annual service, warn_days: 30}
---

# Family car

## Log
- 2025-10-28: Changed to winter tires.
- 2026-04-10: Changed to summer tires. Winter tread measured at 4.5 mm.
