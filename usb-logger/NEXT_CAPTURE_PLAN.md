# Next captures, in priority order

capture01 already contains enumeration, startup (03/04), and regular 0c/13 reads
before the first long service message at t=189.058 s. Analyze and use that existing
startup segment first. A new startup recording is not required to proceed with
read-only polling development. The captures below are supplementary validation.

1. **Optional clean connection without TEST.** Start capture before USB
   connection, launch the terminal, display values for two minutes, then close it.
   Record UTC times and a screen view with a clock. This checks 03/04, initial buffered
   responses, regular 0c/13 requests, and whether long OUT messages occur without manual actions.
2. **Ordinary Z1 room thermostat.** Under normal operating conditions, change only
   user demand ON→OFF→ON, holding each phase for about a minute. Record exact times
   and screenshots of PT1, demand and pump. Compare 0d/25,37 and 14/0,11; retain
   all other differences. Do not provoke protection states or use TEST to operate
   physical relays. The result is still correlation.
3. **PT2 separately**, following the same procedure; inspect 0d/26,38,44 and 14/1,12.
   capture01 has one shared late transition; repetition and timed evidence are needed.
4. **Temperatures and aliases.** Synchronized GUI video/timestamped screenshots and
   a web snapshot with reception time (the web may not provide measurement time).
   Compare gradual changes for each sensor, not one matching value. Do not disconnect
   sensors to test protection behavior.
5. **Signed values:** wait for a natural negative outdoor temperature or use a
   separate laboratory unit. Do not experiment with writes to the live JER.

For each experiment, supply PCAPNG, exact model/firmware, each action and its time,
combustion status and screenshots. Clarify whether capture01 also tested the boiler
pump (index 36), and whether demand changed in Z1 or Z2. Older PCAPs, the PDF manual
and brochure would allow comparison; the current decoder does not require them.

Do not automatically test TEST/actuators, AUTO timeouts, application failure during
override or behavior during firing on the live boiler. These questions require
a separately prepared, qualified laboratory procedure. capture01 cannot guarantee return to AUTO.
