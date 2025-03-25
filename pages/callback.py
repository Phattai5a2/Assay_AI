#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar 25 17:13:19 2025

@author: phattai
"""

import streamlit as st

# Chuyển hướng về trang chính sau khi xử lý callback
st.query_params.clear()
st.rerun()