#include <assert.h>
int main(void) {
    int x = 0;
    for (int i = 0; i < 100; i++) x++;
    assert(x == 100);
    return 0;
}
