"""Workout steps built from laps, and the totals running analytics uses.

A step is a run of consecutive laps with the same step type and Garmin
workout step (wktStepIndex) - e.g. a 3.4 km run split into auto-laps, or
one 15 s stride. Laps of a plain run without workout steps form one step
per step type. The user picks per step what running analytics counts
(ActivityLap.in_analysis); the default is ActivityLap.counts_in_analysis.

Pace is total time / total distance and heart rate is weighted by lap time,
the way Garmin Connect computes them.
"""


def _totals(laps):
    seconds = sum(lap.lap_time_seconds or 0 for lap in laps)
    km = sum(lap.distance_km or 0 for lap in laps)
    hr_laps = [lap for lap in laps if lap.avg_hr and lap.lap_time_seconds]
    hr_seconds = sum(lap.lap_time_seconds for lap in hr_laps)
    return {
        "seconds": seconds,
        "km": round(km, 3),
        "pace": seconds / 60 / km if km > 0 and seconds > 0 else None,
        "avg_hr": round(sum(lap.avg_hr * lap.lap_time_seconds for lap in hr_laps) / hr_seconds) if hr_seconds else None,
        "max_hr": max((lap.max_hr for lap in laps if lap.max_hr), default=None),
    }


def workout_steps(laps, extra_types=()):
    """[{step_type, workout_step, laps, lap_ids, counts, ...totals}] in lap order.

    extra_types: step types (e.g. 'warmup', 'cooldown') the analytics page's
    toggles add on top of the per-workout choice - for laps the user hasn't
    chosen either way on the workout's page."""
    steps = []
    for lap in laps:
        key = (lap.step_type, lap.workout_step)
        if steps and (steps[-1]["step_type"], steps[-1]["workout_step"]) == key:
            steps[-1]["laps"].append(lap)
        else:
            steps.append({"step_type": lap.step_type, "workout_step": lap.workout_step, "laps": [lap]})
    for number, step in enumerate(steps, 1):
        step.update(_totals(step["laps"]))
        step["number"] = number
        step["lap_ids"] = [lap.pk for lap in step["laps"]]
        step["counts"] = all(
            lap.counts_in_analysis or (lap.in_analysis is None and lap.step_type in extra_types)
            for lap in step["laps"]
        )
    return steps


def analysis_totals(laps, extra_types=()):
    """Totals of the laps that count in running analytics, plus the fastest
    counted step's pace (a whole step, so a few-metre auto-lap can't be the
    "best pace"). None when no lap counts."""
    steps = [step for step in workout_steps(laps, extra_types) if step["counts"]]
    if not steps:
        return None
    totals = _totals([lap for step in steps for lap in step["laps"]])
    totals["best_pace"] = min((step["pace"] for step in steps if step["pace"]), default=None)
    totals["lap_count"] = sum(len(step["laps"]) for step in steps)
    return totals
