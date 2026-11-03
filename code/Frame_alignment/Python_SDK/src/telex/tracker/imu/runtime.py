"""Wireless IMU runtime helpers."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import numpy as np

from telex.tracker.constants import DEFAULT_BASE_TO_IMU_BY_HAND, DEFAULT_BASE_TO_IMU_ROTATION
from telex.tracker.imu.fusion import ImuFusionConfig, ImuOrientationFusion
from telex.tracker.imu.receiver import DEFAULT_BAUD, DEFAULT_PORT
from telex.tracker.imu.wireless import start_wireless_hand_subscribers


def _base_to_imu_rotation_for_hand(hand: str) -> np.ndarray:
    key = str(hand).strip().lower()
    matrix = DEFAULT_BASE_TO_IMU_BY_HAND.get(key, DEFAULT_BASE_TO_IMU_ROTATION)
    return np.asarray(matrix, dtype=np.float64).reshape(3, 3).copy()


def make_imu_fusion(args: argparse.Namespace, lever_arm_mm, *, hand: str = "left"):
    try:
        lever_arm = np.asarray(lever_arm_mm, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise ValueError("lever_arm_mm 必须包含 3 个有限数值") from exc
    if lever_arm.shape != (3,) or not np.all(np.isfinite(lever_arm)):
        raise ValueError("lever_arm_mm 必须包含 3 个有限数值")
    lever_arm = lever_arm.copy()

    enable_translation = bool(getattr(args, "imu_translation_fusion", False)) and (
        not bool(getattr(args, "imu_no_translation_fusion", False))
    )

    return ImuOrientationFusion(
        ImuFusionConfig(
            max_age_s=args.imu_max_age_sec,
            imu_hold_max_age_s=args.imu_hold_sec,
            learn_alpha=args.imu_learn_alpha,
            min_learn_frames=args.imu_min_learn_frames,
            near_distance_m=args.imu_near_distance_m,
            far_distance_m=args.imu_far_distance_m,
            low_error_px=args.imu_low_error_px,
            high_error_px=args.imu_high_error_px,
            max_imu_weight=args.imu_max_weight,
            auto_learn_extrinsic=not args.imu_no_auto_learn_extrinsic,
            enable_translation_fusion=enable_translation,
            max_translation_blend_m=args.imu_max_translation_blend_m,
            batch_calibration_samples=args.imu_batch_calibration_samples,
            lever_arm_mm=lever_arm,
            enable_gyro_prediction=not args.imu_no_gyro_prediction,
            base_to_imu_rotation=_base_to_imu_rotation_for_hand(hand),
        )
    )


def _print_cleanup_warning(message: str) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except BaseException:
        pass


def stop_imu_subscribers(subscribers) -> Optional[BaseException]:
    first_error = None
    for hand, subscriber in subscribers.items():
        try:
            subscriber.stop()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            _print_cleanup_warning(
                f"[WARN][IMU:{hand}] 停止订阅失败: {exc}"
            )
    return first_error


def start_dual_imu_runtime(
    args: argparse.Namespace,
    *,
    startup_timeout_s: float = 1.0,
):
    lever_arms = {
        "left": args.imu_left_lever_arm,
        "right": args.imu_right_lever_arm,
    }
    subscribers = {}
    fusions = {}
    provisional_subscribers = {}

    port = args.imu_port if args.imu_port else DEFAULT_PORT
    baud = int(args.imu_baud if args.imu_baud is not None else DEFAULT_BAUD)
    try:
        wireless_subs = start_wireless_hand_subscribers(
            port=port,
            baud=baud,
            offline_timeout_s=args.imu_max_age_sec,
        )
    except Exception as exc:
        print(
            f"[WARN][IMU] wireless hub @ {port}:{baud} 启动失败: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return {}, {}

    for hand in ("left", "right"):
        subscriber = wireless_subs[hand]
        provisional_subscribers[hand] = subscriber
        try:
            fusion = make_imu_fusion(args, lever_arms[hand], hand=hand)
            subscriber.start()
            if not subscriber.wait_ready(timeout_s=startup_timeout_s):
                detail = subscriber.error or (
                    f"启动等待超过 {float(startup_timeout_s):.3f}s"
                )
                raise RuntimeError(detail)
        except Exception as exc:
            failed_subscriber = provisional_subscribers.pop(hand, subscriber)
            if failed_subscriber is not None:
                stop_imu_subscribers({hand: failed_subscriber})
            print(
                f"[WARN][IMU:{hand}] wireless @ {port}:{baud} "
                f"启动失败: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue

        subscribers[hand] = subscriber
        fusions[hand] = fusion
        print(
            f"[IMU:{hand}] wireless 就绪 @ {port}:{baud} "
            f"(base_to_imu=Rz90@Ry45)",
            flush=True,
        )
    return subscribers, fusions
