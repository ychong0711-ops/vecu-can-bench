# vECU-Bench build
CC      ?= gcc
CFLAGS  ?= -O2 -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Iinclude
LDFLAGS ?= -lm

BIN      := bin
LIB_SRCS := src/can_util.c src/sched.c
LIB_OBJS := $(LIB_SRCS:src/%.c=$(BIN)/%.o)
NODES    := ecu_sensor ecu_feature ecu_assistant monitor injector
TARGETS  := $(addprefix $(BIN)/,$(NODES))

# ASAN build: separate output dir so it never pollutes the normal 'bin/'
ASAN_CFLAGS  = -fsanitize=address,undefined -g -O1 -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Iinclude
ASAN_LDFLAGS = -fsanitize=address,undefined -lm
ASAN_BIN     := bin-asan
ASAN_LIB_SRCS := src/can_util.c src/sched.c
ASAN_LIB_OBJS := $(ASAN_LIB_SRCS:src/%.c=$(ASAN_BIN)/%.o)
ASAN_TARGETS  := $(addprefix $(ASAN_BIN)/,$(NODES))

.PHONY: all clean test setup-vcan teardown-vcan demo deps asan lint mutation

all: $(TARGETS)

$(BIN):
	mkdir -p $(BIN)

$(BIN)/%.o: src/%.c include/common.h | $(BIN)
	$(CC) $(CFLAGS) -c $< -o $@

$(BIN)/%: src/%.c $(LIB_OBJS) include/common.h | $(BIN)
	$(CC) $(CFLAGS) $< $(LIB_OBJS) -o $@ $(LDFLAGS)

# Track A1: ecu_feature additionally links the SWC implementation
$(BIN)/ecu_feature: src/ecu_feature.c $(LIB_OBJS) $(BIN)/swc_brake.o \
                    include/common.h include/rte_feature.h include/swc_brake.h | $(BIN)
	$(CC) $(CFLAGS) $< $(LIB_OBJS) $(BIN)/swc_brake.o -o $@ $(LDFLAGS)

# AddressSanitizer + UBSan build — isolated output, no impact on normal 'make'
asan: $(ASAN_TARGETS)

$(ASAN_BIN):
	mkdir -p $(ASAN_BIN)

$(ASAN_BIN)/%.o: src/%.c include/common.h | $(ASAN_BIN)
	$(CC) $(ASAN_CFLAGS) -c $< -o $@

$(ASAN_BIN)/%: src/%.c $(ASAN_LIB_OBJS) include/common.h | $(ASAN_BIN)
	$(CC) $(ASAN_CFLAGS) $< $(ASAN_LIB_OBJS) -o $@ $(ASAN_LDFLAGS)

# Track A1: ASan build of ecu_feature links the SWC implementation too
$(ASAN_BIN)/ecu_feature: src/ecu_feature.c $(ASAN_LIB_OBJS) $(ASAN_BIN)/swc_brake.o \
                         include/common.h include/rte_feature.h include/swc_brake.h | $(ASAN_BIN)
	$(CC) $(ASAN_CFLAGS) $< $(ASAN_LIB_OBJS) $(ASAN_BIN)/swc_brake.o -o $@ $(ASAN_LDFLAGS)

# Python deps for tests + analysis (pytest, pandas, numpy, matplotlib, tabulate, cantools)
deps:
	pip install -r requirements.txt

# cppcheck static analysis + Track A1 layering gate; exits non-zero on any hit
lint:
	@which cppcheck >/dev/null 2>&1 || { \
		echo "cppcheck not found. Install: sudo apt-get install cppcheck  (Debian/Ubuntu)"; \
		echo "or: brew install cppcheck (macOS)"; \
		exit 1; \
	}
	cppcheck --enable=warning,performance,portability --error-exitcode=1 src include
	python3 scripts/check_layering.py

setup-vcan:
	./scripts/setup_vcan.sh

teardown-vcan:
	./scripts/teardown_vcan.sh

test: all
	python3 -m pytest tests/ -v

# Track A2: seed-bug mutation run + report (reports/mutation/)
mutation: all
	python3 scripts/mutate.py

demo: all
	./scripts/run_demo.sh

clean:
	rm -rf $(BIN) $(ASAN_BIN)