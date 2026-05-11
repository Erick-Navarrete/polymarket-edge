"""Tests for the AI agent Kelly Criterion sizing."""

import pytest
from decimal import Decimal

from src.strategies.ai_agent import kelly_fraction


def test_kelly_positive_edge():
    """When estimated prob > market price, Kelly should be positive (buy YES)."""
    # Market price = 0.50, but we estimate 0.70 probability
    kf = kelly_fraction(Decimal("0.70"), Decimal("0.50"))
    assert kf > 0  # Should recommend buying YES


def test_kelly_negative_edge():
    """When estimated prob < market price, Kelly should be negative (buy NO)."""
    # Market price = 0.70, but we estimate only 0.50 probability
    kf = kelly_fraction(Decimal("0.50"), Decimal("0.70"))
    assert kf < 0  # Should recommend buying NO


def test_kelly_no_edge():
    """When estimated prob = market price, Kelly should be ~0."""
    kf = kelly_fraction(Decimal("0.50"), Decimal("0.50"))
    assert abs(kf) < Decimal("0.01")


def test_kelly_extreme_overvaluation():
    """Very overpriced market should produce strong negative Kelly."""
    # Market prices YES at 0.95, but we estimate only 0.50
    kf = kelly_fraction(Decimal("0.50"), Decimal("0.95"))
    assert kf < Decimal("-0.5")


def test_kelly_zero_price():
    """Zero market price should return 0 (edge case)."""
    kf = kelly_fraction(Decimal("0.50"), Decimal("0"))
    assert kf == Decimal("0")


def test_kelly_unit_price():
    """Price of $1 should return 0 (no odds)."""
    kf = kelly_fraction(Decimal("0.50"), Decimal("1"))
    assert kf == Decimal("0")
