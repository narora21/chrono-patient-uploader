APP_NAME := chrono-uploader
SRC := src/main.py
DIST_DIR := dist
BUILD_DIR := build
VENV_DIR := .venv
BUNDLE_FILES := metatag.json README.md

PYTHON := $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)
UNAME := $(shell uname -s)

VENV_PIP := $(VENV_DIR)/bin/pip
VENV_PYINSTALLER := $(VENV_DIR)/bin/pyinstaller

.PHONY: build dist clean install test

$(VENV_DIR):
	$(PYTHON) -m venv $(VENV_DIR)

install: $(VENV_DIR)
	$(VENV_PIP) install -r requirements.txt

build: install
	$(VENV_PYINSTALLER) --onefile --name $(APP_NAME) $(SRC)
	cp $(BUNDLE_FILES) $(DIST_DIR)
	@echo "\nBuilt: $(DIST_DIR)/$(APP_NAME)"

dist: build
ifeq ($(UNAME),Darwin)
	mkdir -p $(DIST_DIR)/$(APP_NAME)-mac
	cp $(BUNDLE_FILES) $(DIST_DIR)/$(APP_NAME)-mac/
	mv $(DIST_DIR)/$(APP_NAME) $(DIST_DIR)/$(APP_NAME)-mac/
	tar -czf $(DIST_DIR)/$(APP_NAME)-mac.tar.gz -C $(DIST_DIR) $(APP_NAME)-mac
	rm -rf $(DIST_DIR)/$(APP_NAME)-mac
	@echo "\nBuilt: $(DIST_DIR)/$(APP_NAME)-mac.tar.gz"
else
	mkdir -p $(DIST_DIR)/$(APP_NAME)-win
	cp $(BUNDLE_FILES) $(DIST_DIR)/$(APP_NAME)-win/
	mv $(DIST_DIR)/$(APP_NAME).exe $(DIST_DIR)/$(APP_NAME)-win/
	cd $(DIST_DIR) && $(PYTHON) -m zipfile -c $(APP_NAME)-win.zip $(APP_NAME)-win
	rm -rf $(DIST_DIR)/$(APP_NAME)-win
	@echo "\nBuilt: $(DIST_DIR)/$(APP_NAME)-win.zip"
endif

test: install
	$(VENV_DIR)/bin/pytest tests/ -v

clean:
	rm -rf $(DIST_DIR) $(BUILD_DIR) $(VENV_DIR) *.spec __pycache__ src/__pycache__
