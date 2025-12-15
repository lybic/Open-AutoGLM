"""
Phone Agent - An AI-powered phone automation framework.

This package provides tools for automating Android phone interactions
using AI models for visual understanding and decision making.

Supports both local ADB connections and Lybic cloud sandbox.
"""

from phone_agent.agent import PhoneAgent, AgentConfig

__version__ = "0.1.0"
__all__ = ["PhoneAgent", "AgentConfig"]
