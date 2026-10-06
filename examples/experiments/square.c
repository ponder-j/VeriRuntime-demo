/*@ requires -46340 <= x <= 46340;
    assigns \nothing;
    ensures \result >= 0;
*/
int square(int x) { return x * x; }
