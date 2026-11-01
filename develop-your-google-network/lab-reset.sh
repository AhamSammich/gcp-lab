#! /bin/bash

set -euo pipefail

red=$'\e[31m'
grn=$'\e[32m'
ylw=$'\e[33m'
blu=$'\e[34m'
cyn=$'\e[36m'
rst=$'\e[0m'

hide=$'\e[?25l'
show=$'\e[?25h'

max() {
  echo "$(($1 > $2 ? $1 : $2))"
}

min() {
  echo "$(($1 < $2 ? $1 : $2))"
}

repeat() {
  local char="$1"
  local n="$2"
  printf "%${n}s" | tr ' ' "$char"
}

# --- Example 1: spinner ---------------------------------------------
# spin_start runs an animation loop in the background subshell;
# each frame starts with \r, which redraws the SAME line in place.
spin_start() {
  local msg="$1"
  local chars='+x'
  printf "%s" "$hide" # hide the cursor so it doesn't flicker
  (
    i=0
    while :; do
      printf "\r%s... %s" "$msg" "${chars:i%4:1}"
      i=$((i + 1))
      sleep 0.1
    done
  ) &
  SPIN_PID=$!
}

# spin_stop kills the loop, then overwrites the frame one last time:
# \r returns to col 0, \e[K wipes any leftover tail, show restores
# the cursor, and \n finally releases the line.
spin_stop() {
  local final_msg="$1"
  kill "$SPIN_PID" 2>/dev/null || true
  wait "$SPIN_PID" 2>/dev/null || true
  SPIN_PID=""
  printf "\r%s\e[K%s\n" "$final_msg" "$show"
}

# if the script dies while the spinner is running (set -e, Ctrl+C),
# the EXIT trap kills it and restores the cursor
cleanup() {
  if [[ -n "${SPIN_PID:-}" ]]; then
    kill "$SPIN_PID" 2>/dev/null || true
    printf "\r\e[K%s\n" "$show"
  else
    printf "%s" "$show"
  fi
}
trap cleanup EXIT

# --- Example 2: same-line counter -----------------------------------
# Instead of printing one line per file, redraw one line with
# \r and a fixed-width [%2d/%d] so the line never "grows".
delete_matching() {
  local color="$1"
  local pattern="$2"
  local label="$3"

  local files=()
  while IFS= read -r f; do
    files+=("$f")
  done < <(find . -type f -name "$pattern" 2>/dev/null)

  local total=${#files[@]}
  if ((total == 0)); then
    printf "%sNo %s files found%s\n" "$grn" "$label" "$rst"
    return 0
  fi

  local i=0
  for f in "${files[@]}"; do
    i=$((i + 1))
    printf "\r%sRemoving %s [%2d/%d] %s%s" "$color" "$label" "$i" "$total" "${f#./}" "$rst"
    rm -f "$f"
  done
  printf "\r%sRemoved %d %s file(s)%s\e[K\n" "$grn" "$total" "$label" "$rst"
}

print_border() {
  local vpad=1
  local hpad=2
  local msg=""
  local border="+"

  # widths are computed up front so -p can validate against them
  local term_width=$(tput cols 2>/dev/null || echo 80)
  local min_width=40
  local max_width=$(($(min "$term_width" 80) - (hpad * 2)))

  while [[ $# -gt 0 ]]; do
    case "$1" in
    -b | --border)
      border="$2"
      shift 2
      ;;
    -p | --pad)
      if [[ ! "$2" =~ ^[0-9]+$ ]]; then
        echo "$1 must be an integer"
        return 1
      fi

      if [[ "$2" -ge "$min_width" && "$2" -le "$max_width" ]]; then
        hpad=$2
      fi
      shift 2
      ;;
    -*)
      echo "Unknown option: $1"
      return 1
      ;;
    *)
      if [[ -z $msg ]]; then
        msg="$1"
      fi
      shift
      ;;
    esac
  done

  # recompute in case -p changed the padding
  max_width=$(($(min "$term_width" 60) - ($hpad * 2)))

  local msg_width=${#msg}
  local seg_width=$(($max_width - $msg_width - $hpad))

  echo "$(repeat "\n" $vpad)"

  echo -n ">"
  repeat $border $hpad
  echo -n "<  $msg  >"
  repeat $border $seg_width
  echo "<"

  echo "$(repeat "\n" $vpad)"
}

print_border -p 1 "EXECUTING LAB RESET"

# Delete Terraform state files
print_border -b "-" "${red}Removing local tfstate...${rst}"
delete_matching "$red" "*.tfstate*" "tfstate"

echo ""

# Delete tfvars
print_border -b "-" "${blu}Unsetting tfvars...${rst}"
delete_matching "$blu" "*.tfvars*" "tfvars"

echo ""

# Unset config and sign out of gcloud CLI
print_border -b "-" "${grn}Unsetting gcloud config...${rst}"
spin_start "${grn}Unsetting gcloud config${rst}"
for cfg in "project" "compute/region" "compute/zone"; do
  gcloud config unset "$cfg" >/dev/null 2>&1
done
spin_stop "${grn}Unsetting gcloud config... done${rst}"

echo ""

print_border -b "-" "${ylw}Revoking credentials...${rst}"
spin_start "${ylw}Revoking ADC${rst}"
gcloud auth application-default revoke >/dev/null 2>&1 || true
spin_stop "${grn}ADC revoked${rst}"

for acct in $(gcloud auth list --format='value(account)' 2>/dev/null | grep student); do
  spin_start "${ylw}Revoking ${acct}${rst}"
  gcloud auth revoke "$acct" >/dev/null
  spin_stop "${grn}Revoked ${acct}${rst}"
done

print_border -p 1 "LAB RESET COMPLETE"
echo ""
