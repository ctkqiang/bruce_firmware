// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <cstdio>
#include <string>
#include <vector>

namespace rf_capture {
// Serialize the capture itself, without repeat detection or timing quantization.
// Flipper RAW files allow at most 512 signed durations on each RAW_Data line.
inline std::string rawLines(const std::vector<int> &durations) {
    std::string out;
    out.reserve(durations.size() * 6 + 16);
    char numBuf[16];
    size_t count = 0;
    for (int duration : durations) {
        if (!duration) continue;
        if (count % 512 == 0) {
            if (count) out.push_back('\n');
            out.append("RAW_Data:");
        }
        int n = snprintf(numBuf, sizeof(numBuf), " %d", duration);
        if (n > 0) out.append(numBuf, static_cast<size_t>(n));
        ++count;
    }
    if (count) out.push_back('\n');
    return out;
}
} // namespace rf_capture
