# Pool Manager for AppDaemon

Smart pool filtration orchestration for Home Assistant through AppDaemon.

The app works **on top of existing Home Assistant integrations**. It does not implement device protocols itself: pump/variable-speed drive, heat pump and chlorination devices remain managed by their dedicated integrations.

Current features include temperature-based filtration, solar-surplus optimization, daily quota tracking, heat-pump flow priority, freeze protection, night circulation, chlorination coordination, certified thermal learning and an adaptive thermal model with full-horizon receding MPC for Automatic and End-of-Season heating. The v0.10 core uses explicit domain composition while preserving the v0.9.1 Home Assistant entity contract.
