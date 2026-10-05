#include <assert.h>

int main(void) {
    int x = 0;
    for (int i = 0; i < 10; ++i)
        x++;
    assert(x == 10);
    return 0;
}
