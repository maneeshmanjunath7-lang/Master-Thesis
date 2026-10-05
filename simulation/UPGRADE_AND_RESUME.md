# Upgrade v2.0 on the Ubuntu VM and resume

The update reuses completed scenario partitions. It does not delete or regenerate valid output. Do not install it while the old Python campaign is still running.

## 1. Stop at the current checkpoint

In the terminal running v2.0, press `Ctrl+C` once and wait for the shell prompt. Completed `case_metrics_part_*.parquet` partitions remain resumable. If the process is running in another terminal, first locate it with:

```bash
pgrep -af "full891 full"
```

## 2. Extract the update beside the existing project

Example:

```bash
cd ~/Downloads/Maneesh
unzip thesis_full_simulation_v2_optimized_v2.1.0.zip -d full891_v21_update
cd full891_v21_update/thesis_full_simulation_v2
chmod +x UPDATE_EXISTING_VM.sh RUN_FULL_VM.sh STATUS_VM.sh setup_vm.sh
```

## 3. Install over the code while preserving outputs

Pass the absolute path of the existing v2.0 project. For the path shown in the VM screenshots:

```bash
./UPDATE_EXISTING_VM.sh /home/maneeshmanjunath/Downloads/Maneesh/thesis_full_simulation_v2
```

The installer creates a dated backup under `update_backups/`, replaces only code/config/launcher files, refreshes the editable Python installation, and does not touch `outputs/`, `logs/`, or `.venv/` data.

## 4. Validate and resume only the full stage

```bash
cd /home/maneeshmanjunath/Downloads/Maneesh/thesis_full_simulation_v2
source .venv/bin/activate
full891 validate --config config/full_campaign.json
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
full891 full --config config/full_campaign.json 2>&1 | tee -a logs/04_full.log
```

Do not run `RUN_FULL_VM.sh` merely to resume, because it repeats validation and the smoke campaign first. The direct `full891 full` command starts at the full campaign and skips checkpointed scenarios.

## 5. Monitor

In a second terminal:

```bash
cd /home/maneeshmanjunath/Downloads/Maneesh/thesis_full_simulation_v2
source .venv/bin/activate
full891 status --config config/full_campaign.json
tail -f logs/04_full.log
```

At startup, inspect `WORKER-PLAN`. On the 29 GB / 50-vCPU VM, the supplied defaults request six workers while reserving 4 GB for Ubuntu. If the VM begins swapping heavily, stop cleanly and lower `max_parallel_base_architectures` to five. Do not increase workers merely because CPU cores are idle: disk swap is far slower than physical RAM and normally makes this workload slower.
